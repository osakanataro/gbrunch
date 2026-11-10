#!/bin/bash
# Build iptsd (linux-surface Intel Precise Touch and Stylus daemon) for Android x86_64 (GBRUNCH /oem/surface).
#
#   surface/build-iptsd.sh WORK          WORK: an empty or previous work directory (about 3 GB with its own NDK;
#                                        set GBRUNCH_NDK to share one, see tools/fetch-android-ndk.sh)
#
# Downloads (pinned and verified): Android NDK r30 (tools/fetch-android-ndk.sh), linux-surface/iptsd at the v3.1.1 commit, its meson wrap
# dependencies (CLI11, Eigen, fmt, inih, Microsoft GSL, spdlog; hashes in the wrap files), and meson/ninja
# into a Python venv. surface/patches/ is applied to iptsd. Everything is linked statically against bionic,
# so the binaries run in Android's user space with no shared libraries. Output: WORK/out, the --surface
# directory of tools/prepare_profile_assets.py (layout of /oem/surface):
#   out/bin/iptsd, out/bin/iptsd-check-device   daemon and device probe
#   out/etc/iptsd.conf                           upstream sample configuration (read at /oem/surface/etc/iptsd.conf)
#   out/share/iptsd/*.conf                       device presets for older Surfaces (Surface Pro 7 reports its own metadata)
#   out/licenses/                                iptsd and dependency license texts
# The output is byte-reproducible (fixed source paths, no build ids, stripped); the script prints every sha256.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
W=$(mkdir -p "${1:?usage: surface/build-iptsd.sh WORK}" && cd "$1" && pwd)
IPTSD_URL=https://github.com/linux-surface/iptsd
IPTSD_COMMIT=106f944ac097f8eb779bdfee0487d58ae853c375   # v3.1.1
[ -x "$W/venv/bin/meson" ] || { python3 -m venv "$W/venv"; "$W/venv/bin/pip" -q install meson==1.9.1 ninja==1.13.0; }
export PATH=$W/venv/bin:$PATH
# Android NDK r30: GBRUNCH_NDK (shared, see tools/fetch-android-ndk.sh) or WORK/android-ndk-r30.
NDK=$("$HERE/../tools/fetch-android-ndk.sh" "${GBRUNCH_NDK:-$W/android-ndk-r30}")
N=$NDK/toolchains/llvm/prebuilt/linux-x86_64
if [ ! -d "$W/iptsd" ]; then
	git clone -q "$IPTSD_URL" "$W/iptsd"
	git -C "$W/iptsd" checkout -q "$IPTSD_COMMIT"
	for p in "$HERE"/patches/*.patch; do git -C "$W/iptsd" apply "$p"; done
fi
[ "$(git -C "$W/iptsd" rev-parse HEAD)" = "$IPTSD_COMMIT" ] || { echo "iptsd checkout is not $IPTSD_COMMIT"; exit 1; }
# Meson downloads the wrap dependencies (sources and wrapdb patches) itself, verifying the hashes in
# subprojects/*.wrap, into subprojects/packagecache. wrapdb.mesonbuild.com answers 504 now and then, so
# retry the download step until every subproject is present.
for attempt in 1 2 3 4 5; do
	meson subprojects download --sourcedir "$W/iptsd" cli11 eigen fmt inih microsoft-gsl spdlog >/dev/null 2>&1 && break
	echo "wrap download attempt $attempt failed; retrying" >&2; sleep 10
done
for d in CLI11 eigen fmt inih GSL spdlog; do ls -d "$W"/iptsd/subprojects/$d-* >/dev/null 2>&1 || { echo "missing wrap dependency $d"; exit 1; }; done
cat > "$W/android.ini" <<EOF
[binaries]
c = '$N/bin/x86_64-linux-android35-clang'
cpp = '$N/bin/x86_64-linux-android35-clang++'
ar = '$N/bin/llvm-ar'
strip = '$N/bin/llvm-strip'

[built-in options]
cpp_args = ['-ffile-prefix-map=$W=/gbrunch', '-ffile-prefix-map=$W/iptsd=/gbrunch/iptsd']
cpp_link_args = ['-static', '-Wl,--build-id=none']

[host_machine]
system = 'android'
cpu_family = 'x86_64'
cpu = 'x86_64'
endian = 'little'
EOF
B=$W/build
# The cross file names the NDK path: set the build directory up again when the NDK moved (GBRUNCH_NDK).
[ "$(cat "$B/.gbrunch-ndk" 2>/dev/null)" = "$NDK" ] || rm -rf "$B"
[ -d "$B" ] || meson setup "$B" "$W/iptsd" --cross-file "$W/android.ini" --prefix=/oem/surface --sysconfdir=/oem/surface/etc \
	--datadir=/oem/surface/share -Dservice_manager= -Ddebug_tools= -Dsample_config=true -Dwerror=false \
	-Dbuildtype=release -Db_lto=false -Ddefault_library=static -Dprefer_static=true > "$W/meson-setup.log"
ninja -C "$B" > "$W/ninja.log"
echo "$NDK" > "$B/.gbrunch-ndk"
rm -rf "$W/stage" "$W/out"; DESTDIR=$W/stage ninja -C "$B" install >/dev/null
mkdir -p "$W/out/bin" "$W/out/etc" "$W/out/share/iptsd" "$W/out/licenses"
for b in iptsd iptsd-check-device; do "$N/bin/llvm-strip" -o "$W/out/bin/$b" "$W/stage/oem/surface/bin/$b"; done
cp "$W/stage/oem/surface/etc/iptsd.conf" "$W/out/etc/"
cp "$W/stage/oem/surface/share/iptsd/"*.conf "$W/out/share/iptsd/"
cp "$W/iptsd/LICENSE" "$W/out/licenses/iptsd-LICENSE.txt"
for d in CLI11 eigen fmt inih GSL spdlog; do
	src=$( (ls -d "$W"/iptsd/subprojects/$d-* 2>/dev/null || true) | head -n1); [ -n "$src" ] || continue
	f=$( (ls "$src"/LICENSE* "$src"/COPYING* 2>/dev/null || true) | head -n1); [ -n "$f" ] && cp "$f" "$W/out/licenses/$d-$(basename "$f")"
done
printf 'iptsd %s (linux-surface, GPL-2.0-or-later) built for Android x86_64 with Android NDK r30 by gbrunch surface/build-iptsd.sh.\nDependencies (static, from the meson wrap files of that commit): CLI11, Eigen, fmt, inih, Microsoft GSL, spdlog.\n' "$IPTSD_COMMIT" > "$W/out/licenses/README.txt"
(cd "$W/out" && find . -type f | sort | xargs sha256sum)
