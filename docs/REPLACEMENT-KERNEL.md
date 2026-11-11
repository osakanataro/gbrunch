# Replacement kernel for a hardware profile

Status: **runs on a Surface Pro 7 (2026-11-09)**. From the device's GBLOG logs: i915 (DMC loaded), the AVX-VNNI
emulator (zmm path) under Google's user space, SAM battery/charger (98 %, AC), Bluetooth firmware `ibt-19-32-4`,
USB Ethernet (RTL8153, after adding the USB NIC modules), IPTS HID device and the setup wizard all work. Found and
fixed from the logs: (1) libcutils opens `/dev/ashmem` unless `sys.use_memfd` is true → kernel profiles set it at
early-init; (2) this unit's AX201 is a Qu C-step and asks for `iwlwifi-Qu-c0-hr-b0-77.ucode`, not QuZ → all three
34f0 variants are shipped; (3) the NHLT has no DMICs, so SOF wants `sof-hda-generic.tplg` → added; (4) `iptsd` as uid
root in the shell domain is denied CAP_DAC_OVERRIDE on the system-owned hidraw nodes → it runs as uid system with
groups uhid/input. Fixes (2)-(4) were confirmed on the device (Wi-Fi connects, touch works). Audio needed two more things: (5) the SOF HDA generic
board always has DMIC links and waits for the `dmic-codec` component, so `snd-soc-dmic` must be loaded (the card stayed
"deferred probe pending" without it); (6) DRAS configuration for the `sof-hda-dsp` card and the `/vendor/etc` fallback
XMLs, as on the Dragonfly (mixer control names provisional until the `gbrunch.audiodump` snapshot provides them). **Speaker output, the internal
microphone and the 3.5 mm headphone jack with its headset microphone were confirmed on the device on 2026-11-10.** The Headphone section of the DRAS
`audio_config.xml` must carry `<JackSwitch>2</JackSwitch>` (SW_HEADPHONE_INSERT): without it DRAS rejects the whole file
(`missing field JackSwitch`) and nothing plays. Not yet done: suspend/resume, cameras (IPU4P work in progress). First profile: Microsoft Surface Pro 7 (Ice
Lake), `profiles/surface-pro-7/`. SMBIOS confirmed: vendor `Microsoft Corporation`, product and board `Surface Pro 7`. QEMU TCG (`-cpu Skylake-Client`, no AVX-VNNI) on 2026-11-09:
`6.18.21-gbrunch-sp7` boots, all 57 modules of the profile's closures load (`surface_gpe` returns
ENODEV without Surface DMI, as expected), the AVX-VNNI user test passes all forms with the built-in
emulator (100,000 emulated instructions, 1 declined `ud2`), `/dev/ashmem` is absent as intended (memfd shim).
The Dragonfly profile derivation is unchanged by this work (same pinned `compat.ext4`). A raw image was
assembled from the derived assets with the kit's assembler (tool VM, local recovery): the EFI partition
holds the replacement kernel, the initrd carries the i915 closure and `icl_dmc_ver1_09.bin` (Google's own
ramdisk modules stay in the initrd but are not in `modules.load`, so they are never loaded), GRUB gates on
the Surface Pro 7 SMBIOS strings. `iptsd` (surface/build-iptsd.sh, byte-reproducible across two work directories)
is in `/oem/surface` with its init service. First hardware test planned from a USB stick (dd + `grow-userdata.sh`).

GBRUNCH normally boots Google's own kernel and loads Google's modules. Some laptops cannot work that way:

- GPUs older than Tiger Lake need `i915`, which the stock kernel does not have and which cannot be built
  as an out-of-tree module (it needs 131 symbols the stock KMI does not export).
- CPUs without AVX-VNNI (Ice Lake, Tiger Lake) fault on the first instruction of Google's `init`; the
  emulator has to be in the kernel before the first-stage init runs, so a module is too late.
- Surface Aggregator, SOF on Ice Lake, IPTS and similar drivers are not among Google's modules.

A *kernel profile* replaces the kernel with one built from the **same ACK revision** as the stock kernel
(`3938c8d97142`, `android17-6.18`, Clang `r584948c`), starting from the recovery's embedded config, so
the kernel/user-space ABI Google's Android expects (binder devices, dm-verity, erofs, f2fs, cgroups, ...)
is unchanged. Only the hardware side changes. Google's ramdisk, system, vendor and the derived GBVENDOR
(SELinux enforcing, software KeyMint/Gatekeeper) are the same as for any other profile. No Google kernel
module can load on the replacement kernel (different release and symbol CRCs), so every module comes
from the same build, and `init.gbrunch.rc` loses its `insmod` lines for Google's modules.

## Pieces

| Where | What |
| --- | --- |
| `gki-kernel/common.config` | Overrides for every kernel profile: `RUST=n` with the C binder and the memfd ashmem shim (plus `setprop sys.use_memfd true` in `init.gbrunch.rc`, added by `prepare_profile_assets.py` for every kernel profile; `gki-kernel/test/ashmem_memfd_test.c` checks the shim in QEMU), exports untrimmed and unprotected, no debug info, `DMABUF_HEAPS_SYSTEM`/`ACPI_AC`/`ACPI_BATTERY` built in, the AVX-VNNI emulator built in, `SERIAL_8250_RUNTIME_UARTS=4`. |
| `gki-kernel/patches/` | Kernel patches (GPL-2.0, origins in `gki-kernel/README.md`). |
| `profiles/<name>/kernel.json`, `kernel.config` | The profile's release suffix, fragments, patches, first-stage (ramdisk) modules and the options that must resolve. |
| `tools/build_profile_kernel.py` | Builds `kernel` (bzImage), `modules/` (stripped, depmod'ed), `ramdisk-modules/` (closure of the GPU driver) and `kernel.json` (hashes of every input and output, resolved config differences) in a detached git worktree of the ACK checkout. |
| `tools/test_profile_kernel_qemu.py` | QEMU TCG smoke test: boots the kernel, insmods the module closures, runs the AVX-VNNI user test on a CPU model without AVX-VNNI. |
| `tools/prepare_profile_assets.py --kernel DIR` | Puts the kernel, its first-stage modules and `/oem/modules` into a profile asset directory; marks `replacement_kernel` in `native-assets.json`; strips Google-module `insmod` lines from `init.gbrunch.rc`; expands `oem_modules` `module` entries to dependency closures. |
| `tools/prepare_profile_assets.py --surface DIR`, profile `policy_rules` | Puts `surface/build-iptsd.sh` output into `/oem/surface`; `policy_rules` (source, target, class, perms) are added to the derived GBVENDOR sepolicy by the tool VM, the same way as the base GBRUNCH rules. |
| `tools/assemble_native_android.py` | Uses the kit's kernel and modules when `replacement_kernel` is present (Google's kernel hash is still recorded); the GRUB entry says which kernel it loads. |

## Kits

The USB kits are profile-neutral: `profiles/<profile>/kit.json` (image name, laptop firmware keys, notes, `kernel`
flag) is copied into the asset directory, and `tools/usb/kit_profile.py` renders the kit READMEs from it.
`tools/usb/make-dev-kit.sh ASSETS IMAGE KITDIR` builds or updates the developer kit, one folder for all profiles
(e.g. `work/usb-kit`): shared `usb-tools/`, `profile-tools/` and `src/` (refreshed from the checkout on every run),
and per profile `PROFILE/` with the assets, the GBVENDOR image extracted from the disk image, the image hash and a
README whose commands run from the kit folder; `KITDIR/README.txt` lists the profiles. `tools/usb/make-usb-kit.sh`
in a release kit does the same for end users (one profile, `usb/`). `tools/usb/apply-image-update.sh` copies EFI-SYSTEM, GBRUNCH and
GBVENDOR from a new image onto a disk written from the same layout, which is how a kernel update reaches a
replacement-kernel profile without a new `dd` (userdata, metadata and GBLOG are kept). With `--system` it also
writes Google's system partitions (super, boot, init_boot, vendor_boot, vbmeta, pvmfw, recovery, ota_recovery, KERN-B,
ROOT-B) where they differ, which moves an installed disk to a newer official recovery while keeping userdata.

Recovery builds: `tools/assemble_external_lab.py` lists the known official Ruby recoveries (`RECOVERIES`, newest
first); `assemble_native_android.py` takes any of them by hash (`--recovery-raw`/`--recovery-zip`) and downloads
the newest, or `--build`. 16452207 (CL3B.260622.271.R1) and 16693588 (CL3B.260622.276.R5, 2026-11-06) share the
GPT, the logical partition extents in super (vendor_a at 6289358848) and a byte-identical kernel; the newer one
changes apps, APEXes (including the DRAS audio HAL), the HWC and build properties, not init rc files, VINTF, the
SELinux policy, fstab or kernel modules.

## Build

Prerequisites: the `tools/build_gki_modules.py` work tree (`WORK/source` = git checkout of the ACK commit,
`WORK/clang-r584948c`), the recovery's `kernel.config` and `vmlinux` (`tools/extract-ikconfig`,
`tools/extract-vmlinux`), about 8 GiB free, `make`, `flex`, `bison`, `bc`, `libssl-dev`, `libelf-dev`.

```sh
python3 tools/build_profile_kernel.py --work WORK --config kernel.config --vmlinux vmlinux \
    --profile surface-pro-7 --out kernel-sp7 --compile
python3 tools/test_profile_kernel_qemu.py --build kernel-sp7 --out kernel-sp7/qemu-smoke.json \
    --modules iwlmvm btusb snd_hda_codec_alc269 snd_hda_codec_intelhdmi snd_soc_skl_hda_dsp snd_sof_pci_intel_icl surface_aggregator_registry ipts
python3 tools/prepare_profile_assets.py --assets ASSETS-V7 --output assets-surface-pro-7 \
    --profile surface-pro-7 --kernel kernel-sp7
```

The build is pinned: the release is `6.18.21-gbrunch-sp7` (no `+`), timestamps and user/host are fixed.
Modules are signed with a key generated at build time, so module bytes differ between builds; the
kernel image itself should not.

## Surface Pro 7 profile

Hardware table (from the 6.18 driver tables and linux-surface; on the device 2026-11-09/10 display, setup, touch/pen,
Wi-Fi, Bluetooth firmware, USB Ethernet, battery, speakers, internal mic, 3.5 mm headphones and headset mic work):

| Device | Driver | Firmware (profile) | Notes |
| --- | --- | --- | --- |
| Iris Plus Gen11 `8086:8a52`/`8a56` | `i915` (ramdisk) | `i915/icl_dmc_ver1_09.bin` | GuC/HuC are off by default on Gen11 |
| AX201 CNVi `8086:34f0` (this unit: Qu C-step, rev 0x332) | in-tree `iwlwifi`/`iwlmvm` | `iwlwifi-Qu-c0-hr-b0-77.ucode` (+ Qu-b0, QuZ-a0 for other steppings) | Google's iwl7000 build is not used |
| AX201 Bluetooth `8087:0026` | `btusb` | `intel/ibt-19-0-4.sfi/.ddc` (+ `19-32-4` alias) | |
| Battery, charger, performance profile | `surface_aggregator` + registry (`MSHW0116`/`MSHW0119`, mainline), `surface_battery`, `surface_charger` | | SAM on the LPSS UART: `SERIAL_8250_DW`, `RUNTIME_UARTS=4` |
| Power/volume buttons, lid wake | `soc_button_array`, `surface_gpe` | | |
| Type Cover | USB HID | | linux-surface typecover patch not applied (does not apply) |
| Audio ALC298 (no DMIC in NHLT), HDMI | SOF Ice Lake, `skl_hda_dsp_generic`, Realtek/HDMI codecs | `intel/sof/sof-icl.ri`, `sof-hda-generic.tplg` | `snd_intel_dspcfg.dsp_driver=3`; options for init-loaded modules must go on the `insmod` line, the kernel command line is only read by modprobe; DRAS XML fallbacks still to be written |
| Touchscreen / pen | IPTS (`ipts`, patch 0006) + `iptsd` in `/oem/surface` (`surface/build-iptsd.sh`, NDK static build; init service `gbrunch.iptsd` in the shell domain with profile SELinux rules) | | creates uinput touchscreen/stylus devices; untested on hardware |
| Cameras: front ov5693, rear ov8865 (+ dw9719 VCM), IR ov7251 | IPU4P `8086:8a19` via `intel-ipu6` (patches 0007-0020: media-next IPU driver + ruslanbay's IPU4P series + sensor backports), `ipu-bridge`, `intel_skl_int3472_discrete` | `intel/ipu/ipu4p_fw.bin`: Microsoft's, not redistributable; `surface/fetch-ipu4p-firmware.sh` + `assemble_native_android.py --local-files` put it only into the locally built GBVENDOR (the assets carry an empty placeholder) | libcamera simple pipeline + softISP through the GBRUNCH camera HAL. First hardware test 2026-11-10: rear streams (was upside down and its brightness oscillated), front all green (its 2x2-binned 1296x972 mode gives no frames). Rear orientation fixed by camera/patches 0003 (HAL keeps the sensor's native orientation, reported as 180); brightness: 0002 (ov5693/ov8865 sensor delays) and 0004 (exposure in whole 10 ms flicker periods, tuning `flickerPeriod`). Front: works (stills, 1296x972). Rear: kernel patches 0022 (ov8865 SCLK pixel rate, vblank reset), 0023 (gain in 1/128 steps: no pumping) and 0024 (no packed RAW on IPU4P: the video mode had picked SBGGR10P and got no frames). camera/patches/0005 reads libcamera log levels from etc/libcamera/log-levels; kernel patch 0021 allows `dyndbg=+p` on intel-ipu6-isys. IR and autofocus not configured (dw9719 answers -121) |
| Display density | | | the profile sets `qemu.sf.lcd_density 240` after the base 160 (12.3" 2736x1824, 267 dpi): a 1824x1216dp desktop, and the 10dp scrollbar overlay at a usable size |
| microSD | `sdhci-pci` | | |

Boot menu: the profile is admitted when the command line carries `androidboot.gbrunch_kernel=gbrunch-sp7`
(only a build with this kernel has it), vendor `Microsoft Corporation`, product `Surface Pro 7`, board
`Surface Pro 7` (board string to be confirmed from the device's SMBIOS; the experimental entry boots
regardless).

Known open items before hardware: pin the derived `compat.ext4` hash (`compat_sha256`,
`dualboot_installer.COMPAT_HASHES`), DRAS audio XML, the zmm register path of the AVX-VNNI
emulator (QEMU has no AVX-512; Ice Lake uses it), and i915 against Google's drm_hwcomposer (GBRUNCH already
disables the colour pipeline and log gamma LUT properties that gbOS86 had to patch out of i915).
