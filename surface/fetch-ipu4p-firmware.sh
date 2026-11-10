#!/bin/bash
# Fetch the Surface Pro 7 camera (IPU4P) firmware from Microsoft's official Surface Pro 7 driver package and
# extract it locally. The firmware is Microsoft's and is NOT redistributable: it never enters a GBRUNCH kit.
# assemble_native_android.py --local-files WORK/out puts it into the locally built GBVENDOR partition.
#
#   surface/fetch-ipu4p-firmware.sh WORK      (about 1.4 GB in WORK while extracting; 4 MB kept)
#
# Source: SurfacePro7_Win11_22621_25.090.3489.0.msi from download.microsoft.com (pinned sha256). Its cab1.cab
# holds the Intel camera driver's cpd_component_signed.bin (Intel camera package 42.18362.3.16827) under the
# MSI file key below; the result is checked against the sha256 the linux-surface community uses
# (github.com/ruslanbay/ipu4-drivers). Needs 7-Zip: the system 7z/7zz, or the Ubuntu 7zip package fetched with
# "apt download" into WORK and unpacked there (no root, nothing installed).
# Output: WORK/out/intel/ipu/ipu4p_fw.bin
set -euo pipefail
W=$(mkdir -p "${1:?usage: surface/fetch-ipu4p-firmware.sh WORK}" && cd "$1" && pwd)
MSI_URL=https://download.microsoft.com/download/b7ea0d32-93fb-4733-b78c-4263b314dbf4/SurfacePro7_Win11_22621_25.090.3489.0.msi
MSI_SHA256=16d072797526b6be42a9e2633d760664dcc864163122c8248ba57fc64578d354
CAB=cab1.cab
KEY=fil04D9073F54C5356FDD0D1055A71C3D98
FW_SHA256=ff2c36cc81a5c726508b22970c2e2538ff06107dc5a72c93401403c227e5157f
out=$W/out/intel/ipu/ipu4p_fw.bin
if [ -f "$out" ] && echo "$FW_SHA256  $out" | sha256sum -c --quiet - 2>/dev/null; then echo "$out (already present)"; exit 0; fi
Z=$(command -v 7zz || command -v 7z || true)
if [ -z "$Z" ]; then
	if [ ! -x "$W/7zip/usr/lib/7zip/7z" ]; then
		(cd "$W" && rm -f 7zip_*.deb && apt download 7zip >/dev/null) || { echo "need 7-Zip: sudo apt install 7zip"; exit 1; }
		dpkg-deb -x "$W"/7zip_*.deb "$W/7zip"
	fi
	Z=$W/7zip/usr/lib/7zip/7z
fi
msi=$W/$(basename "$MSI_URL")
if [ ! -f "$msi" ] || ! echo "$MSI_SHA256  $msi" | sha256sum -c --quiet - 2>/dev/null; then
	echo "Downloading $MSI_URL (678 MB)"
	curl -fL --retry 3 -o "$msi.part" "$MSI_URL"; mv "$msi.part" "$msi"
	echo "$MSI_SHA256  $msi" | sha256sum -c --quiet -
fi
tmp=$(mktemp -d "$W/extract.XXXXXX"); trap 'rm -rf "$tmp"' EXIT
"$Z" e -y -o"$tmp" "$msi" "$CAB" >/dev/null
"$Z" e -y -o"$tmp" "$tmp/$CAB" "$KEY" >/dev/null
echo "$FW_SHA256  $tmp/$KEY" | sha256sum -c --quiet - || { echo "extracted firmware hash mismatch"; exit 1; }
mkdir -p "$(dirname "$out")"; mv "$tmp/$KEY" "$out"; chmod 0644 "$out"
echo "$out"; echo "The MSI ($msi) can be deleted now; it is only re-downloaded if out/ is missing."
