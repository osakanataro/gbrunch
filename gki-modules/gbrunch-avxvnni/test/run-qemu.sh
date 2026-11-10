#!/bin/bash
# Test gbrunch_avxvnni.ko on the stock GKI kernel in QEMU with a CPU model that lacks AVX-VNNI.
# TCG implements AVX2 but not AVX-512, so this exercises the ymm register path only; the zmm path
# taken on Ice Lake / Tiger Lake needs hardware.
# Usage: bash run-qemu.sh KERNEL(bzImage from EFI/GBRUNCH/kernel) gbrunch_avxvnni.ko ACK-SOURCE-DIR
set -euo pipefail
kernel=$(realpath "${1:?bzImage}"); ko=$(realpath "${2:?gbrunch_avxvnni.ko}"); src=$(realpath "${3:?ACK source dir}")
here=$(cd "$(dirname "$0")" && pwd); w=$(mktemp -d)
gcc -O2 -static -mavx2 -mavxvnni -o "$w/vnni_test" "$here/vnni_test.c"
gcc -O2 -static -o "$w/init" "$here/init.c"
gcc -O2 -o "$w/gen_init_cpio" "$src/usr/gen_init_cpio.c"
cat > "$w/list" <<EOF
dir /dev 0755 0 0
dir /sys 0755 0 0
nod /dev/console 0600 0 0 c 5 1
nod /dev/ttyS0 0600 0 0 c 4 64
file /init $w/init 0755 0 0
file /vnni_test $w/vnni_test 0755 0 0
file /gbrunch_avxvnni.ko $ko 0644 0 0
EOF
"$w/gen_init_cpio" "$w/list" | gzip -1 > "$w/initrd.gz"
# 8250.nr_uarts=4: the GKI config registers no serial ports by default; its built-in cmdline sets console=ttynull.
timeout 900 qemu-system-x86_64 -machine q35 -accel tcg -cpu Skylake-Client -m 1024 -smp 2 -nographic -no-reboot \
	-kernel "$kernel" -initrd "$w/initrd.gz" \
	-append "console=ttyS0,115200 8250.nr_uarts=4 rdinit=/init loglevel=5 panic=-1" 2>&1 \
	| grep -v "TCG doesn't support" | tee "$w/qemu.log" | grep -E "init:|GBRUNCH|FAIL|PASSED|FAILED|instructions:"
grep -q "vnni_test PASSED" "$w/qemu.log"
