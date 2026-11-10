#!/bin/bash
# Build tinyalsa's tinymix for Android x86_64 (static, bionic) into WORK/out/bin/tinymix, next to the iptsd build.
#
#   surface/build-tinymix.sh WORK      WORK: the surface/build-iptsd.sh work directory (reuses its NDK, or GBRUNCH_NDK)
#
# tinymix dumps the ALSA mixer controls of the SOF card on the device; the Surface Pro 7 profile runs it once
# at boot (service gbrunch.audiodump) and logs the output through logcat, which the GBLOG touch/audio
# snapshots collect, so the DRAS mixer_paths.xml can be written from real control names without a shell
# on the device. Pinned: tinyalsa commit bbdba1e2 (BSD-3-Clause). Byte-reproducible (no build id, stripped).
set -euo pipefail
W=$(mkdir -p "${1:?usage: surface/build-tinymix.sh WORK}" && cd "$1" && pwd)
TINYALSA_URL=https://github.com/tinyalsa/tinyalsa
TINYALSA_COMMIT=bbdba1e208f402c5b7d5cb3d42307a3db9f6bb1c
NDK=${GBRUNCH_NDK:-$W/android-ndk-r30}   # the NDK surface/build-iptsd.sh used (GBRUNCH_NDK if shared)
N=$NDK/toolchains/llvm/prebuilt/linux-x86_64
[ -x "$N/bin/x86_64-linux-android35-clang" ] || { echo "no Android NDK r30 at $NDK: run surface/build-iptsd.sh $W first, with the same GBRUNCH_NDK"; exit 1; }
if [ ! -d "$W/tinyalsa" ]; then
	git clone -q "$TINYALSA_URL" "$W/tinyalsa"
	git -C "$W/tinyalsa" checkout -q "$TINYALSA_COMMIT"
fi
[ "$(git -C "$W/tinyalsa" rev-parse HEAD)" = "$TINYALSA_COMMIT" ] || { echo "tinyalsa checkout is not $TINYALSA_COMMIT"; exit 1; }
mkdir -p "$W/out/bin" "$W/out/licenses"
"$N/bin/x86_64-linux-android35-clang" -O2 -static -Wl,--build-id=none -ffile-prefix-map="$W"=/gbrunch \
	-I"$W/tinyalsa/include" -o "$W/out/bin/tinymix.unstripped" "$W/tinyalsa/utils/tinymix.c" "$W"/tinyalsa/src/*.c -ldl
"$N/bin/llvm-strip" -o "$W/out/bin/tinymix" "$W/out/bin/tinymix.unstripped"; rm -f "$W/out/bin/tinymix.unstripped"
cp "$W/tinyalsa/NOTICE" "$W/out/licenses/tinyalsa-NOTICE.txt" 2>/dev/null || cp "$W/tinyalsa/LICENSE" "$W/out/licenses/tinyalsa-LICENSE.txt"
sha256sum "$W/out/bin/tinymix" "$W"/out/licenses/tinyalsa-*
