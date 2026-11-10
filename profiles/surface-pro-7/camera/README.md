Surface Pro 7 camera configuration for the GBRUNCH libcamera HAL (camera/build.sh stack), added to /oem/camera:

- `lib64/etc/libcamera/camera_hal.yaml`: front OV5693 (`\_SB_.PCI0.I2C2.CAMF`) and rear OV8865 (`\_SB_.PCI0.I2C3.CAMR`).
- `lib64/share/libcamera/ipa/softisp/ov5693.yaml`, `ov8865.yaml`: software ISP tuning, CC0-1.0, from
  https://github.com/ruslanbay/ipu4-drivers (configs/), originally https://github.com/ConsultingFuture4200/sp7-camera.

Hardware notes (2026-11-10): both sensors report rotation 180 (ACPI SSDB). camera/patches/0003 keeps frames in that
native orientation so apps rotate them once; camera/patches/0002 adds the sensors' 2-frame exposure/gain delays
(the default gain delay of 1 made the AGC oscillate); camera/patches/0004 with `flickerPeriod: 10000` in both tuning
files keeps exposure times to whole 10 ms periods (50 Hz mains LED lighting; 8333 for 60 Hz). Both cameras take
stills (2026-11-10; the front ov5693 at its binned 1296x972 mode, the full-crop experiment was dropped). The rear
delivers no frames in video mode (preview 1280x720 + video 1280x720); lib64/etc/libcamera/log-levels (camera/patches/0005)
raises SimplePipeline and Camera to DEBUG for that. gki-kernel/patches/0021 builds the IPU6 driver with dynamic debug;
add 'args':'dyndbg=+p' to intel_ipu6_isys in the profile for the per-frame CSI-2 events (they flood dmesg).

Rear camera fixes (2026-11-10, kernel patches): 0022 ov8865 PIXEL_RATE = SCLK (libcamera line time, so the 10 ms
flicker rounding is right) and vblank reset on mode change; 0023 ov8865 analogue gain in 1/128 steps (a 1x step made
the gain jump between 1x and 2x: slow brightness pumping); 0024 IPU4P offers no packed RAW10/RAW12 capture formats
(the video mode picked SBGGR10P, whose MIPI output pin never got a frame from the 4-lane rear camera; the unpacked
RAW_SOC path works).
