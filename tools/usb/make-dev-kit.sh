#!/bin/bash
# Build or update the developer USB kit: one folder for every profile, copied to a data USB stick and used from a
# live Linux (work/usb-kit). The tools are shared; each profile has its own subdirectory.
#
#   tools/usb/make-dev-kit.sh ASSETS IMAGE KITDIR [--kernel-work DIR] [--surface-work DIR] [--vendor-erofs FILE]
#     ASSETS   tools/prepare_profile_assets.py output (holds kit.json)
#     IMAGE    assemble_native_android.py --format raw output built from ASSETS, or "-" to skip the image and the
#              GBVENDOR extraction (an existing PROFILE/<vendor erofs> is then kept, or --vendor-erofs is copied)
#     KITDIR   new or existing kit folder, e.g. work/usb-kit
#
#   KITDIR/README.txt, NOTICE.txt   index of the profiles in the kit, license notices
#   KITDIR/usb-tools/                tools/usb (apply-*.sh, grow-userdata.sh, ...): the same for every profile
#   KITDIR/profile-tools/            this branch's tools/*.py
#   KITDIR/src/                      profiles/, gki-kernel/, surface/ (sources)
#   KITDIR/PROFILE/                  README.txt (commands for that laptop), assets-PROFILE/, the GBVENDOR image,
#                                    the disk image hash; kernel profiles also qemu-smoke.json and surface-out/
# The shared parts are refreshed from this checkout on every run, so all profiles in the kit use the same tools;
# only PROFILE/ of the given assets is replaced. The disk image itself is referenced, not copied (8 GB).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$here/../.." && pwd)
usage="usage: make-dev-kit.sh ASSETS IMAGE KITDIR [--kernel-work DIR] [--surface-work DIR] [--vendor-erofs FILE]"
assets=$(realpath "${1:?$usage}"); img=${2:?$usage}; kit=${3:?$usage}; shift 3
kwork=; swork=; vfile=
while [ $# -gt 0 ]; do case "$1" in
	--kernel-work) kwork=$2; shift 2;; --surface-work) swork=$2; shift 2;; --vendor-erofs) vfile=$2; shift 2;;
	*) echo "unknown option $1"; exit 1;; esac; done
[ -f "$assets/kit.json" ] || { echo "$assets has no kit.json (regenerate it with tools/prepare_profile_assets.py)"; exit 1; }
get() { python3 "$here/kit_profile.py" "$assets" get "$1"; }
profile=$(get profile); image=$(get image); vendor=$(get vendor_erofs); kernel=$(get kernel)
mkdir -p "$kit"; kit=$(realpath "$kit"); pdir=$kit/$profile

# Shared parts
rm -rf "$kit/usb-tools" "$kit/profile-tools" "$kit/src"
mkdir -p "$kit/usb-tools" "$kit/profile-tools" "$kit/src"
cp "$here"/*.sh "$here/kit_profile.py" "$kit/usb-tools/"
for f in prepare_profile_assets.py googlebook_boot_menu.py dualboot_installer.py build_profile_kernel.py build_amd_kernel_modules.py \
         test_profile_kernel_qemu.py assemble_native_android.py build_gki_modules.py gki_symvers.py android_persistlogs.sh; do
	[ -f "$repo/tools/$f" ] && cp "$repo/tools/$f" "$kit/profile-tools/"
done
cp -r "$repo/profiles" "$repo/gki-kernel" "$kit/src/"; cp -r "$repo/surface" "$kit/src/" 2>/dev/null || true
cp "$repo/tools/usb/kit/NOTICE.txt" "$kit/"

# This profile's directory
mkdir -p "$pdir"
keep=$(mktemp -d); trap 'rm -rf "$keep"' EXIT
if [ "$img" = - ] && [ -z "$vfile" ] && [ -f "$pdir/$vendor" ]; then mv "$pdir/$vendor" "$pdir/$vendor.sha256" "$keep/" 2>/dev/null || true; fi
rm -rf "$pdir"; mkdir -p "$pdir"
cp -r "$assets" "$pdir/assets-$profile"; rm -rf "$pdir/assets-$profile/__pycache__"
if [ "$kernel" = True ]; then
	[ -n "$kwork" ] && cp "$kwork/qemu-smoke.json" "$pdir/" 2>/dev/null || true
	[ -n "$swork" ] && cp -r "$swork/out" "$pdir/surface-out"
fi
if [ "$img" != - ]; then
	img=$(realpath "$img"); [ -f "$img" ] || { echo "$img is not a file"; exit 1; }
	manifest="$img.manifest.json"; [ -f "$manifest" ] || { echo "$manifest missing (assemble output)"; exit 1; }
	python3 - "$img" "$manifest" "$pdir/$vendor" "$assets" <<'EOF'
import json,sys,hashlib,subprocess,pathlib
img,manifest,out,assets=sys.argv[1:]
m=json.load(open(manifest))
want=json.load(open(pathlib.Path(assets)/'native-assets.json'))['files']['compat.ext4']
gv=[p for p in m['appended_partitions'] if p['name']=='GBVENDOR'][0]
gb=[p for p in m['appended_partitions'] if p['name']=='GBRUNCH'][0]
h=hashlib.sha256();f=open(img,'rb');f.seek(gb['offset']);h.update(f.read(gb['bytes']))
if h.hexdigest()!=want:sys.exit('image GBRUNCH partition %s is not the asset compat.ext4 %s: rebuild the image from these assets'%(h.hexdigest()[:16],want[:16]))
subprocess.run(['dd','if='+img,'of='+out,'bs=1M','iflag=skip_bytes,count_bytes','skip=%d'%gv['offset'],'count=%d'%m['vendor']['vendor_bytes'],'status=none'],check=True)
v=hashlib.file_digest(open(out,'rb'),'sha256').hexdigest()
if v!=m['vendor']['vendor_sha256']:sys.exit('extracted GBVENDOR hash mismatch')
pathlib.Path(out+'.sha256').write_text('%s  %s\n'%(v,pathlib.Path(out).name))
print('GBVENDOR -> %s (%d bytes, %s)'%(out,m['vendor']['vendor_bytes'],v[:16]))
EOF
	[ -f "$img.sha256" ] || (cd "$(dirname "$img")" && sha256sum "$(basename "$img")" > "$img.sha256")
	sed "s|  $(basename "$img")\$|  $image|" "$img.sha256" > "$pdir/$image.sha256"
	imgline="Disk image (not in the kit, 8 GB real): $img ; its sha256 is in $profile/$image.sha256."
else
	if [ -n "$vfile" ]; then
		cp "$vfile" "$pdir/$vendor"; (cd "$pdir" && sha256sum "$vendor" > "$vendor.sha256")
	elif [ -f "$keep/$vendor" ]; then mv "$keep/$vendor" "$keep/$vendor.sha256" "$pdir/" 2>/dev/null || true; fi
	imgline="Disk image: not given to make-dev-kit.sh."
	[ -f "$pdir/$vendor" ] && imgline="$imgline $vendor taken from the previous kit or --vendor-erofs."
fi

# Profile README: the release-kit template with this kit's paths (commands run from the kit folder)
python3 "$here/kit_profile.py" "$assets" render "$here/kit/README-usb-kit.txt" "$pdir/README.txt.tmp"
{ echo "GBRUNCH developer kit, profile $profile  (tools/usb/make-dev-kit.sh, $(date -u +%Y-%m-%d), repo $(git -C "$repo" rev-parse --short HEAD 2>/dev/null || echo ?)$(git -C "$repo" diff --quiet 2>/dev/null || echo ' +uncommitted'))"
  echo "  $imgline"
  echo "  Run the commands below from the kit folder (the one holding usb-tools/), not from $profile/."
  echo
  sed -e "s|usb/|usb-tools/|g" -e "s|assets-$profile|$profile/assets-$profile|g" -e "s|\([ ]\)$vendor|\1$profile/$vendor|g" \
      -e "s|sha256sum -c $image.sha256|sha256sum -c $profile/$image.sha256   (run from the folder holding $image)|" "$pdir/README.txt.tmp"
} > "$pdir/README.txt"; rm "$pdir/README.txt.tmp"

# Index of the profiles in the kit
{ echo "GBRUNCH developer kit (tools/usb/make-dev-kit.sh): one folder for every laptop profile."
  echo
  echo "  usb-tools/      apply-image-update.sh, apply-profile-update.sh, apply-vendor-update.sh, grow-userdata.sh,"
  echo "                  reset-firstboot.sh: the same for every profile (usb/ of a release kit)"
  echo "  profile-tools/  this branch's tools; src/ = profiles/, gki-kernel/, surface/ sources"
  echo
  echo "Profiles (see PROFILE/README.txt; run its commands from this folder):"
  for d in "$kit"/*/; do
	k=; for f in "$d"assets-*/kit.json; do [ -f "$f" ] && k=$f; done; [ -n "$k" ] || continue
	python3 - "$k" "$(basename "$d")" <<'EOF'
import json,sys
k=json.load(open(sys.argv[1]))
print('  %-18s %s (image %s)'%(sys.argv[2]+'/',k.get('device',k['profile']),k['image']))
EOF
  done
  echo
  echo "Updated $(date -u +%Y-%m-%d) for $profile; tools from repo $(git -C "$repo" rev-parse --short HEAD 2>/dev/null || echo ?)$(git -C "$repo" diff --quiet 2>/dev/null || echo ' +uncommitted')."
} > "$kit/README.txt"
echo "Developer kit: $kit ($profile/)"; du -sh "$kit" | cut -f1
