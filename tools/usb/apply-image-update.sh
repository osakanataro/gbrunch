#!/bin/bash
# Update an existing Googlebook disk from a newly assembled image: copy its EFI-SYSTEM (kernel, initrd,
# grub.cfg), GBRUNCH (/oem) and GBVENDOR partitions over the disk's. userdata, metadata and GBLOG are kept,
# Google's partitions are not touched (they are identical anyway). This is the update path for a
# replacement-kernel profile (the kernel and its first-stage modules live in the EFI partition and the initrd),
# and a one-step alternative to apply-profile-update.sh + apply-vendor-update.sh for the others.
# The disk must have been written from an image of the same layout (dd), so the partitions have the same
# sizes; an installer-created layout with a shared EFI partition is refused for the EFI part.
# If the image's GBVENDOR is larger, GBLOG (right after it) is shifted and reformatted, its files kept.
# USB disks only, unless --internal is given (an internal disk the image was written to).
# Usage: sudo bash apply-image-update.sh [--internal] /dev/sdX|/dev/nvmeXnY image.img
set -euo pipefail
internal=0; [ "${1:-}" = --internal ] && { internal=1; shift; }
d=${1:?usage: sudo bash apply-image-update.sh [--internal] /dev/sdX|/dev/nvmeXnY image.img}; img=${2:?image.img}
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
copy EFI-SYSTEM; copy GBRUNCH; grow_vendor; copy GBVENDOR
sync; echo "done: EFI-SYSTEM, GBRUNCH and GBVENDOR replaced from $img (userdata, metadata and GBLOG kept)"
