#!/bin/bash
# Update an existing Googlebook disk from a newly assembled image: copy its EFI-SYSTEM (kernel, initrd,
# grub.cfg), GBRUNCH (/oem) and GBVENDOR partitions over the disk's. userdata, metadata and GBLOG are kept;
# Google's partitions are not touched unless --system is given (below). This is the update path for a
# replacement-kernel profile (the kernel and its first-stage modules live in the EFI partition and the initrd),
# and a one-step alternative to apply-profile-update.sh + apply-vendor-update.sh for the others.
# The disk must have been written from an image of the same layout (dd), so the partitions have the same
# sizes; an installer-created layout with a shared EFI partition is refused for the EFI part.
# If the image's GBVENDOR is larger, GBLOG (right after it) is shifted and reformatted, its files kept.
# --system: also bring Google's partitions to the image's recovery build (a newer official Ruby recovery with the
# same layout, e.g. 16452207 -> 16693588): super (system, vendor, product), boot, init_boot, vendor_boot, vbmeta,
# pvmfw, recovery, ota_recovery, KERN-B and ROOT-B, each only when it differs from the disk. userdata, metadata, misc,
# frp_persist, desktop_security_* and GBLOG are never written, so apps and settings are kept, like an OTA update.
# Only move forward: Android does not support going back to an older build on the same userdata.
# USB disks only, unless --internal is given (an internal disk the image was written to).
# Usage: sudo bash apply-image-update.sh [--internal] [--system] /dev/sdX|/dev/nvmeXnY image.img
set -euo pipefail
internal=0; system=0
while [ $# -gt 0 ]; do case "$1" in --internal) internal=1; shift;; --system) system=1; shift;; *) break;; esac; done
d=${1:?usage: sudo bash apply-image-update.sh [--internal] [--system] /dev/sdX|/dev/nvmeXnY image.img}; img=${2:?image.img}
[ "$(lsblk -dno TYPE "$d")" = disk ] || { echo "$d is not a whole disk"; exit 1; }
tran=$(lsblk -dno TRAN "$d")
if [ "$internal" = 1 ]; then [ "$tran" != usb ] || { echo "$d is a USB disk; drop --internal"; exit 1; }
else [ "$tran" = usb ] || { echo "$d is not a USB disk (use --internal for an internal disk)"; exit 1; }; fi
[ -z "$(lsblk -no MOUNTPOINTS "$d" | tr -d '[:space:]')" ] || { echo "Unmount all partitions of $d first"; exit 1; }
[ -f "$img" ] || { echo "$img is not a file"; exit 1; }
# assemble_native_android.py writes IMAGE.manifest.json last: without it (or with an older one) the image is
# still being assembled or the assembly failed, and its partitions are incomplete.
[ -f "$img.manifest.json" ] && [ ! "$img.manifest.json" -ot "$img" ] || { echo "$img has no up-to-date $img.manifest.json: the image is incomplete (still being assembled?); wait for the assembly to finish"; exit 1; }
# "start size" in bytes of a named partition in the image's GPT
img_part() { sfdisk -J "$img" | python3 -c '
import json,sys
d=json.load(sys.stdin)["partitiontable"];ss=d.get("sectorsize",512)
p=[x for x in d["partitions"] if x.get("name")==sys.argv[1]]
if len(p)!=1:sys.exit("need exactly one %s partition in the image"%sys.argv[1])
print(p[0]["start"]*ss,p[0]["size"]*ss)' "$1"; }
disk_part() { local p; p=$(lsblk -lnpo NAME,PARTLABEL "$d" | awk -v n="$1" '$2==n{print $1}'); [ "$(echo "$p" | wc -w)" = 1 ] || { echo "need exactly one '$1' partition on $d"; exit 1; }; echo "$p"; }
for n in GBLOG userdata; do disk_part $n >/dev/null; done   # a Googlebook disk, not an arbitrary one
copy() {  # NAME
	local p start size psize want
	p=$(disk_part "$1"); read -r start size < <(img_part "$1"); psize=$(blockdev --getsize64 "$p")
	[ "$size" = "$psize" ] || { echo "$1: image partition is $size bytes, $p is $psize bytes (different layout); refusing"; exit 1; }
	want=$(dd if="$img" bs=1M iflag=skip_bytes,count_bytes skip="$start" count="$size" status=none | sha256sum | cut -c1-64)
	echo "$1: $p <- $img@$start ($size bytes, sha256 $want)"
	dd if="$img" of="$p" bs=1M iflag=skip_bytes,count_bytes skip="$start" count="$size" conv=fsync status=none
	[ "$(head -c "$size" "$p" | sha256sum | cut -c1-64)" = "$want" ] || { echo "$1 readback mismatch"; exit 1; }
}
sysfs() { cat "/sys/class/block/$(basename "$1")/$2"; }   # partition, start, size (512-byte units)
# A newer image can carry a larger GBVENDOR (e.g. the Surface Pro 7 camera firmware). GBLOG (FAT, diagnostic logs
# only) directly follows it on disks written from these images, so GBLOG's start moves up by the difference; its
# files are copied out, the partition is reformatted as assemble_native_android.py does and the files are put back.
grow_vendor() {
	local v l vn ln vstart vsize lstart lsize need delta vtype vguid ltype lguid tmp
	v=$(disk_part GBVENDOR); l=$(disk_part GBLOG)
	vn=$(sysfs "$v" partition); ln=$(sysfs "$l" partition)
	vstart=$(sysfs "$v" start); vsize=$(sysfs "$v" size); lstart=$(sysfs "$l" start); lsize=$(sysfs "$l" size)
	read -r _ need < <(img_part GBVENDOR); need=$((need / 512))
	[ "$need" -gt "$vsize" ] || return 0
	delta=$((need - vsize))
	[ "$lstart" = $((vstart + vsize)) ] || { echo "GBVENDOR must grow by $delta sectors but GBLOG does not follow it; refusing"; exit 1; }
	[ $((lsize - delta)) -ge 131072 ] || { echo "GBLOG too small to give $delta sectors to GBVENDOR; refusing"; exit 1; }
	[ "$(blockdev --getss "$d")" = 512 ] || { echo "$d does not use 512-byte sectors"; exit 1; }
	echo "GBVENDOR: growing $v by $((delta / 2048)) MiB; GBLOG $l starts later and is reformatted (its files are kept)"
	vtype=$(sgdisk -i "$vn" "$d" | awk '/Partition GUID code/{print $4}'); vguid=$(sgdisk -i "$vn" "$d" | awk '/Partition unique GUID/{print $4}')
	ltype=$(sgdisk -i "$ln" "$d" | awk '/Partition GUID code/{print $4}'); lguid=$(sgdisk -i "$ln" "$d" | awk '/Partition unique GUID/{print $4}')
	tmp=$(mktemp -d); mkdir "$tmp/mnt" "$tmp/files"
	mount -o ro "$l" "$tmp/mnt"; cp -r "$tmp/mnt/." "$tmp/files/"; umount "$tmp/mnt"
	echo "  GBLOG files saved: $(du -sh "$tmp/files" | cut -f1) in $tmp/files"
	sgdisk -d "$ln" -d "$vn" \
		-n "$vn:$vstart:$((vstart + need - 1))" -t "$vn:$vtype" -u "$vn:$vguid" -c "$vn:GBVENDOR" \
		-n "$ln:$((lstart + delta)):$((lstart + lsize - 1))" -t "$ln:$ltype" -u "$ln:$lguid" -c "$ln:GBLOG" "$d" >/dev/null
	partprobe "$d"; udevadm settle
	l=$(disk_part GBLOG)
	for m in $(lsblk -no MOUNTPOINTS "$d" | tr -s '[:space:]' ' '); do umount "$m"; done   # desktop automount after partprobe
	mkfs.fat -F 32 -n GBRUNCH_LOG "$l" >/dev/null
	mount "$l" "$tmp/mnt"; cp -r "$tmp/files/." "$tmp/mnt/"; umount "$tmp/mnt"
	rm -rf "$tmp"; echo "  GBLOG reformatted and its files restored"
}
GOOGLE=(super boot_a boot_b init_boot_a init_boot_b vendor_boot_a vendor_boot_b vbmeta_a vbmeta_b pvmfw_a pvmfw_b
        recovery_a recovery_b ota_recovery_a ota_recovery_b KERN-B ROOT-B)
copy_if_changed() {  # NAME: copy only when the disk partition differs from the image's
	local p start size have want
	p=$(disk_part "$1"); read -r start size < <(img_part "$1")
	[ "$size" = "$(blockdev --getsize64 "$p")" ] || { echo "$1: image and disk sizes differ (different layout); refusing"; exit 1; }
	want=$(dd if="$img" bs=1M iflag=skip_bytes,count_bytes skip="$start" count="$size" status=none | sha256sum | cut -c1-64)
	have=$(head -c "$size" "$p" | sha256sum | cut -c1-64)
	if [ "$want" = "$have" ]; then echo "$1: unchanged"; else copy "$1"; fi
}
img_uuid() { sfdisk -J "$img" | python3 -c '
import json,sys
p=[x for x in json.load(sys.stdin)["partitiontable"]["partitions"] if x.get("name")==sys.argv[1]]
print(p[0]["uuid"].lower())' "$1"; }
disk_uuid() { lsblk -no PARTUUID "$(disk_part "$1")" | tr 'A-Z' 'a-z'; }
# The image's GRUB passes androidboot.boot_part_uuid (boot_a's GUID) and each official recovery build has its own
# partition GUIDs: an EFI partition from another build than the disk's Google partitions never finds its boot device.
if [ "$system" = 1 ]; then
	for n in "${GOOGLE[@]}"; do disk_part "$n" >/dev/null; img_part "$n" >/dev/null; done   # all present before writing
elif [ "$(img_uuid boot_a)" != "$(disk_uuid boot_a)" ]; then
	echo "$img is from another recovery build than $d (boot_a GUID $(img_uuid boot_a), disk $(disk_uuid boot_a)): use --system"; exit 1
fi
copy EFI-SYSTEM; copy GBRUNCH; grow_vendor; copy GBVENDOR
if [ "$system" = 1 ]; then
	echo "Google partitions (super is 12 GiB: reading and writing it takes a while):"
	for n in "${GOOGLE[@]}"; do copy_if_changed "$n"; done
	# Give the Google partitions the image's GUIDs (boot_part_uuid on the kernel command line names boot_a's).
	guids=()
	for n in "${GOOGLE[@]}"; do
		want=$(img_uuid "$n"); [ "$want" = "$(disk_uuid "$n")" ] && continue
		guids+=(-u "$(sysfs "$(disk_part "$n")" partition):$want")
	done
	if [ ${#guids[@]} -gt 0 ]; then
		echo "partition GUIDs: $(( ${#guids[@]} / 2 )) set to the image's"
		sgdisk "${guids[@]}" "$d" >/dev/null; partprobe "$d"; udevadm settle
		[ "$(img_uuid boot_a)" = "$(disk_uuid boot_a)" ] || { echo "boot_a GUID not updated"; exit 1; }
	fi
fi
sync
if [ "$system" = 1 ]; then echo "done: EFI-SYSTEM, GBRUNCH, GBVENDOR and Google's system partitions updated from $img (userdata, metadata and GBLOG kept)"
else echo "done: EFI-SYSTEM, GBRUNCH and GBVENDOR replaced from $img (userdata, metadata and GBLOG kept)"; fi
