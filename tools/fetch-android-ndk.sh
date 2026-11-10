#!/bin/bash
# Make sure an unpacked Android NDK r30 exists at NDKDIR (download, verify and unpack it if not) and print NDKDIR.
#
#   tools/fetch-android-ndk.sh NDKDIR
#
# The GBRUNCH Android builds (camera/build.sh, surface/build-iptsd.sh, surface/build-tinymix.sh) use one NDK: set
# GBRUNCH_NDK=/path/android-ndk-r30 to share a single copy (2.3 GB) between their work directories; without it each
# work directory gets its own WORK/android-ndk-r30 as before. The download (705 MB, pinned sha256) is removed once
# unpacked. The build outputs do not depend on where the NDK is.
set -euo pipefail
NDK_URL=https://dl.google.com/android/repository/android-ndk-r30-linux.zip
NDK_SHA256=753611f410d002cfcd3f3dc2ef49aad532089d3180b436c060a90bf0fcb64df2
ndk=${1:?usage: tools/fetch-android-ndk.sh NDKDIR}
if [ -x "$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/x86_64-linux-android35-clang" ] &&
   grep -q '^Pkg.Revision = 30\.' "$ndk/source.properties" 2>/dev/null; then
	realpath "$ndk"; exit 0
fi
[ ! -e "$ndk" ] || { echo "$ndk exists but is not an Android NDK r30; remove it first" >&2; exit 1; }
parent=$(mkdir -p "$(dirname "$ndk")" && cd "$(dirname "$ndk")" && pwd)
tmp=$(mktemp -d "$parent/.ndk-download.XXXXXX"); trap 'rm -rf "$tmp"' EXIT
echo "Downloading $NDK_URL (705 MB)" >&2
curl -fL --retry 3 -o "$tmp/ndk.zip" "$NDK_URL" >&2
echo "$NDK_SHA256  $tmp/ndk.zip" | sha256sum -c --quiet -
unzip -q "$tmp/ndk.zip" -d "$tmp"
mv "$tmp/android-ndk-r30" "$parent/$(basename "$ndk")"
realpath "$ndk"
