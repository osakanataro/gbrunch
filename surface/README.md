# Surface user-space pieces

`build-iptsd.sh WORK` builds [linux-surface/iptsd](https://github.com/linux-surface/iptsd) (the daemon that turns
the raw IPTS heatmaps of Surface touchscreens into multitouch and stylus input) for Android x86_64 with the
pinned Android NDK r30, statically linked against bionic. `patches/` is applied to the pinned iptsd commit.
`WORK/out` is the `--surface` input of `tools/prepare_profile_assets.py` and lands in `/oem/surface`:

| Path | Purpose |
| --- | --- |
| `bin/iptsd`, `bin/iptsd-check-device` | daemon and device probe |
| `etc/iptsd.conf` | upstream sample configuration, read from `/oem/surface/etc/iptsd.conf` |
| `share/iptsd/*.conf` | device presets for Surfaces that do not report metadata (the Pro 7 does) |
| `licenses/` | iptsd (GPL-2.0-or-later) and dependency license texts |

The Surface Pro 7 profile starts it through `profiles/surface-pro-7/oem/surface/iptsd.sh` (init service
`gbrunch.iptsd`, class `late_start`, uid root, SELinux domain `shell`): the wrapper waits for the IPTS hidraw
device created by `ipts.ko`, probes it with `iptsd-check-device -t touchscreen` and execs `iptsd` on it. The
profile's `policy_rules` give the shell domain the hidraw node, `/dev/uinput` and the `/oem/surface` files.
Diagnostics on the device: `dmesg | grep gbrunch-iptsd`, `ls -lZ /dev/hidraw*`, `getevent -i` (the uinput
devices appear as "IPTS Touchscreen" and "IPTS Stylus").

The output is byte-reproducible (fixed prefix maps, no build ids, stripped); the hashes are pinned in the
profile's `oem_surface` list. The binaries also run on a Linux host for a quick `--help` check.
