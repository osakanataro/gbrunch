#!/bin/bash
# Build the Googlebook disk image for the profile in this release kit, from Google's official recovery.
# The kit (tools/usb/package-release.sh output) holds only open-source and redistributable files; this
# script downloads the recovery (or takes a local recovery.zip), checks its hash, and runs the kit's
# assemble_native_android.py, which derives the kernel, ramdisk and GBVENDOR locally in a QEMU build VM.
# Runs as a normal user on x86_64 Linux. Needs about 50 GB free in OUTDIR.
# OUTDIR/usb-kit/ is then copied to a data USB stick for the steps in usb-kit/README.txt.
# Usage: bash make-usb-kit.sh [--recovery-zip recovery.zip] OUTDIR
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
zip=
[ "${1:-}" = --recovery-zip ] && { zip=$(realpath "${2:?recovery.zip}"); shift 2; }
out=${1:?usage: bash make-usb-kit.sh [--recovery-zip recovery.zip] OUTDIR}
assets=$(ls -d "$here"/assets-* 2>/dev/null | head -n1)
[ -n "$assets" ] && [ -f "$here/SHA256SUMS" ] || { echo "Run this from an unpacked release kit (assets-*/ and SHA256SUMS not found)"; exit 1; }

missing=
for t in python3 qemu-system-x86_64 qemu-img qemu-io mcopy mformat lz4 sha256sum; do command -v "$t" >/dev/null || missing="$missing $t"; done
if [ -n "$missing" ]; then
	echo "Missing programs:$missing"
	echo "Ubuntu/Debian: sudo apt install python3 qemu-system-x86 qemu-utils mtools lz4"
	exit 1
fi

echo "Checking the kit files..."
(cd "$here" && sha256sum -c --quiet SHA256SUMS) || { echo "Kit files do not match SHA256SUMS; download the kit again"; exit 1; }

[ -f "$assets/kit.json" ] || { echo "$assets has no kit.json"; exit 1; }
profile=$(python3 "$here/usb/kit_profile.py" "$assets" get profile); image=$(python3 "$here/usb/kit_profile.py" "$assets" get image)
vendor=$(python3 "$here/usb/kit_profile.py" "$assets" get vendor_erofs)
mkdir -p "$out"; out=$(realpath "$out")
kit=$out/usb-kit; img=$kit/$image
[ ! -e "$img" ] || { echo "$img already exists; remove it or use another OUTDIR"; exit 1; }
need=$((50*1024*1024))  # KiB; recovery zip 7.8 GB + raw 19 GB (sparse) + image 19 GB
[ -n "$zip" ] && need=$((42*1024*1024))
[ -f "$out/recovery-cache/ruby-recovery.raw" ] && need=$((22*1024*1024))  # rebuild: image only
free=$(df -Pk "$out" | awk 'NR==2{print $4}')
[ "$free" -ge "$need" ] || { echo "Not enough space in $out: $((free/1024/1024)) GiB free, about $((need/1024/1024)) GiB needed"; exit 1; }

mkdir -p "$kit"
src=(--download); [ -n "$zip" ] && src=(--recovery-zip "$zip")
echo "Building the image (downloads 7.8 GB unless --recovery-zip is given)..."
python3 "$assets/assemble_native_android.py" "${src[@]}" --cache "$out/recovery-cache" \
	--assets "$assets" --format raw --output "$img"
mv "$img.build-vm.log" "$out/" 2>/dev/null || true
mv "$img.manifest.json" "$out/$image.manifest.json"

echo "Writing the image hash..."
(cd "$kit" && sha256sum "$image" > "$image.sha256")
echo "Extracting the GBVENDOR image for apply-vendor-update.sh..."
python3 - "$img" "$out/$image.manifest.json" "$kit/$vendor" <<'EOF'
import json,sys,hashlib,subprocess
img,manifest,out=sys.argv[1:]
m=json.load(open(manifest));gv=[p for p in m['appended_partitions'] if p['name']=='GBVENDOR'][0]
subprocess.run(['dd','if='+img,'of='+out,'bs=1M','iflag=skip_bytes,count_bytes','skip=%d'%gv['offset'],'count=%d'%m['vendor']['vendor_bytes'],'status=none'],check=True)
assert hashlib.file_digest(open(out,'rb'),'sha256').hexdigest()==m['vendor']['vendor_sha256'],'GBVENDOR hash mismatch'
EOF
mkdir -p "$kit/usb"
cp "$here"/usb/*.sh "$here/usb/kit_profile.py" "$kit/usb/"
python3 "$here/usb/kit_profile.py" "$assets" render "$here/README-usb-kit.txt" "$kit/README.txt"
echo
echo "Done: $kit"
echo "Copy that folder to a data USB stick formatted as exFAT or ext4 (the image is larger than 4 GB),"
echo "then follow $kit/README.txt."
echo "The downloaded recovery stays in $out/recovery-cache for a later rebuild; delete it to free about 27 GB."
echo "The image contains Google's recovery: do not share it."
