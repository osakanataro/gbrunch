# Replacement-kernel inputs

Inputs for `tools/build_profile_kernel.py`, which builds a kernel for a hardware profile from the exact
Android Common Kernel revision of the stock Googlebook kernel (`3938c8d97142`, `android17-6.18`) with
the pinned Clang (`r584948c`). See [docs/REPLACEMENT-KERNEL.md](../docs/REPLACEMENT-KERNEL.md).

- `common.config`: Kconfig fragment applied to the recovery's embedded config for every kernel profile.
  A profile adds its own `profiles/<name>/kernel.config` and lists both in `profiles/<name>/kernel.json`.
- `patches/`: git patches applied to a clean worktree of the ACK commit before configuring.

| Patch | Origin | License |
| --- | --- | --- |
| `0001-mm-memfd-ashmem-shim.patch` | gbOS86 (`kernel/ashmem_shim/`, https://github.com/linuxkid473/gbos-x86), converted to a git patch. C port of the ACK Rust ashmem memfd ioctls for kernels built with `RUST=n`. | GPL-2.0 |
| `0002-x86-gbrunch-avxvnni-builtin.patch` | GBRUNCH `gki-modules/gbrunch-avxvnni/gbrunch_avxvnni.c` (identical source, built in; `tests/test_gki_kernel.py` checks that). | GPL-2.0 |
| `0003-linux-surface-surface-sam.patch` | linux-surface `patches/6.18/0007-surface-sam.patch` (Surface Aggregator additions, RTC). | GPL-2.0 |
| `0004-linux-surface-surface-button.patch` | linux-surface `patches/6.18/0009-surface-button.patch`. | GPL-2.0 |
| `0005-linux-surface-surface-gpe.patch` | linux-surface `patches/6.18/0012-surface-gpe.patch`. | GPL-2.0 |
| `0006-linux-surface-ipts-ack-6.18.patch` | linux-surface `patches/6.18/0005-ipts.patch` (IPTS touchscreen/pen: `drivers/hid/ipts`, MEI id 0x34E4, IOMMU passthrough), rebased by GBRUNCH: MEI macro names and IOMMU hunks around this tree's pKVM changes; HID files unchanged. | GPL-2.0 |

Not included: linux-surface `0010-surface-typecover.patch` does not apply to this ACK revision (`hid-multitouch.c`) and the
Surface Pro 7 Type Cover works as plain USB HID without it.

A kit built with such a kernel ships GPL-2.0 binaries (bzImage and modules); the corresponding source is the
ACK commit above plus these patches and fragments, recorded with hashes in the kit's `kernel.json`.
