#!/usr/bin/env python3
"""Derive a hardware-profile asset directory from an existing GBRUNCH asset kit.

The source kit is read-only. A NEW output directory receives a copy with:
  * pinned linux-firmware GPU files added to the initramfs firmware list,
  * pinned Wi-Fi/Bluetooth firmware written into a copy of compat.ext4 (/oem/firmware),
  * native-grub.cfg admission regenerated from googlebook_boot_menu.PROFILES,
  * native-assets.json file hashes updated and a profile record added,
  * with --kernel DIR (tools/build_profile_kernel.py output): the profile's replacement kernel, its
    first-stage modules (manifest 'modules', read by the kit's assembler instead of extracting Google's)
    and its other modules in /oem/modules, loaded by init.gbrunch.rc in dependency order.
Use the output with assemble_native_android.py --assets OUTPUT.
No Google recovery bytes are read or written, and no block devices are touched.
"""
import argparse,base64,gzip,hashlib,io,json,os,pathlib,tarfile,re,shutil,subprocess,sys,tempfile,urllib.request
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import googlebook_boot_menu
from googlebook_boot_menu import smbios_gate
from build_profile_kernel import module_closure

COMMIT='788aadc8f73d93908cf8545b2fc770fb6580a3f2'  # same linux-firmware commit as download_hp_radio_firmware.py
SOURCE='https://gitlab.com/kernel-firmware/linux-firmware/-/raw/'+COMMIT+'/'
FIRMWARE_LABEL='u:object_r:vendor_firmware_file:s0'
BASE_COMPAT='77cb6ec0e399a944a020b5d7d55d03dbd1bb5a2ba01012be91d315987ab69722'  # tested V7 compat.ext4
# Fixed e2fsprogs clock so the derived compat.ext4 is byte-reproducible and can be
# pinned in dualboot_installer.COMPAT_HASHES (2025-10-10T05:00:00Z, close to the V7 build).
FAKE_TIME='1760072400'
# Debian packages used as pinned firmware sources (entries with 'deb' and 'member').
DEBS={
 'firmware-sof-signed':{'url':'https://deb.debian.org/debian/pool/non-free-firmware/f/firmware-sof/firmware-sof-signed_2026.09.2-1_all.deb',
                        'sha256':'eb9ff4928b739ae88e63261839299951a66b27a01e573622e0213ac14f0aa609'},
}
# Extra logger capture: backlight sysfs and ALSA state, for brightness/audio bring-up.
LOGGER_ANCHOR='''    capture "$n-wifi.txt" dumpsys -t 5 wifi
'''
LOGGER_CAPTURE='''    capture "$n-backlight-audio.txt" sh -c 'for d in /sys/class/backlight/*; do echo "== $d"; for f in type max_brightness brightness actual_brightness bl_power; do echo "$f=$(cat $d/$f)"; done; done; cat /proc/asound/cards /proc/asound/pcm; ls -l /dev/snd; dumpsys -t 5 media.audio_flinger | head -200; dumpsys -t 5 media.audio_policy | head -200'
    capture "$n-audio-logcat.txt" sh -c 'logcat -b all -d -v threadtime | grep -iE "audio|dras|APM_|soundcard|hal_?config|sofhdadsp|sof-hda|tinyalsa|mixer|alsa" | tail -c 1048576'
    capture "$n-audio-kernel.txt" sh -c 'dmesg | grep -iE "cs35l41|cs_dsp|sof-audio|snd_hda|alc269|ehdaudio|hdac|skl_hda" | tail -c 262144'
    capture "$n-camera.txt" sh -c 'dmesg | grep -iE "ipu6|hi556|int3472|ov01|ovti|isys" | tail -60; ls -l /sys/bus/i2c/drivers/hi556 /sys/bus/platform/drivers/int3472-discrete /sys/bus/pci/drivers/intel-ipu6 2>&1; ls -lZ /dev/dma_heap /dev/video* /dev/media* /dev/v4l-subdev* 2>&1; for v in /sys/class/video4linux/*; do echo "$v $(cat $v/name)"; done; dumpsys -t 5 media.camera | head -150; p=$(pidof android.hardware.camera.provider@2.7-service-legacy-wrapper); [ -n "$p" ] && top -b -n 1 -H -p $p | head -30; logcat -b main,system -d -v threadtime -s gbcam:V libcamera:V gbrunch-camera:V camera_hal_adapter_AidlCameraProvider:V camera_hal_adapter_AidlCameraService:V CameraService:V CameraProviderManager:V | tail -c 393216'
    capture "$n-battery.txt" sh -c 'dumpsys -t 5 battery; for d in /sys/class/power_supply/*; do echo "== $d -> $(readlink -f $d)"; for f in type online status capacity; do echo "$f=$(cat $d/$f)"; done; done'
'''
PROFILE_DIR=pathlib.Path(__file__).resolve().parents[1]/'profiles'
INIT_ANCHOR='''    mount none /oem/weaver-manifest.xml /vendor/etc/vintf/manifest/android.hardware.weaver-service.android-desktop.xml bind
'''
# target: 'initrd' -> /lib/firmware in the generated ramdisk (needed before /oem mounts),
#         'oem'    -> /oem/firmware (init.gbrunch.rc sets firmware_class.path there),
#         'license'-> licenses/ in the asset directory.
PROFILES={
 # Microsoft Surface Pro 7 (Ice Lake). Needs the profile's replacement kernel (profiles/surface-pro-7/kernel.json,
 # tools/build_profile_kernel.py --compile; pass --kernel DIR): Gen11 graphics (i915) and AVX-VNNI emulation are not
 # possible with Google's kernel. No stock (Ruby vendor_dlkm) module can load on it, so every module comes from
 # the kernel build: the GPU closure in the first-stage ramdisk, the rest in /oem/modules via init.gbrunch.rc.
 # Hardware not yet booted; firmware names from the drivers' MODULE_FIRMWARE declarations and linux-surface.
 'surface-pro-7':{
  'board':'Surface Pro 7',
  'compat_sha256':'580a190c3d2d7377c9745531b58c1a0be5d6a2fd0b1a97c4914b63459754565b',  # kernel 6.18.21-gbrunch-sp7 modules + firmware + DRAS audio (speaker, internal mic, 3.5 mm jack) + iptsd + tinymix + sys.use_memfd + IPU4P camera (ov5693/ov8865, libcamera softISP; firmware local-only) + density 240 + camera fixes (sensor delays, native orientation, 10 ms flicker period, HAL log-levels file, ov8865 SCLK pixel rate, V4L2/DelayedControls debug (logger log-levels file) + periodic kernel log, ov8865 fine gain, ISYS dyndbg, filtered kernel log, IPU4P unpacked RAW only), binned max width, full-width source, debug off
  'kernel':True,
  'stock_modules':False,
  'evidence':'kernel 6.18 driver tables (i915 ICL, iwlwifi QuZ, surface_aggregator_registry sp7, surface_gpe DMI) and linux-surface; no device log yet',
  # androidboot.gbrunch_kernel: marks a build that carries this replacement kernel (boot menu admission, ro.boot.gbrunch_kernel).
  # dsp_driver=3: SOF (snd_soc_avs would otherwise claim the Ice Lake DSP). Note that options for modules loaded by init's
  # insmod are NOT taken from the kernel command line (only modprobe does that); the Pro 7 needs none (no DMIC).
  'cmdline_append':'androidboot.gbrunch_kernel=gbrunch-sp7 snd_intel_dspcfg.dsp_driver=3',
  'logger_capture':True,
  # Touchscreen/pen: iptsd (surface/build-iptsd.sh, --surface WORK/out) under /oem/surface, started by the wrapper
  # below once ipts.ko has created the IPTS hidraw device. It runs in the shell domain (like the GBRUNCH logger) as
  # uid system with groups uhid/input, which own the device nodes, so no CAP_DAC_OVERRIDE is needed; the rules give
  # that domain the IPTS hidraw node, /dev/uinput and the /oem/surface binaries.
  'oem_files':[('surface/iptsd.sh','vendor_configs_file'),('surface/audiodump.sh','vendor_configs_file'),
               # DRAS: Ruby's HAL config names the sofrt5682 card and never finds it ("internal card not found", device
               # log 2026-11-10); these point it at the SOF HDA generic card like the Dragonfly profile does.
               ('audio/hal_audio.xml','vendor_configs_file'),('audio/audio_config.xml','vendor_configs_file'),
               ('audio/mixer_paths.xml','vendor_configs_file')],
  'init_binds':[('/oem/audio/hal_audio.xml','/vendor/etc/data/hal_configs/audio_default.xml'),
                ('/oem/audio/hal_audio.xml','/vendor/etc/data/hal_configs/audio_alc5682i_tas2563rppr.xml'),
                ('/oem/audio/audio_config.xml','/vendor/etc/audio/sof-rt5682_alc5682ivs_tas2563/audio_config.xml'),
                ('/oem/audio/mixer_paths.xml','/vendor/etc/audio/sof-rt5682_alc5682ivs_tas2563/mixer_paths.xml'),
                ('/oem/audio/mixer_paths.xml','/vendor/etc/mixer_paths.xml'),
                ('/vendor/firmware/intel/ipu/ipu4p_fw.bin','/oem/firmware/intel/ipu/ipu4p_fw.bin'),
                ('/oem/camera/lib64','/vendor/lib64/libcamera')],
  # qemu.sf.lcd_density: the base init sets 160 (HP 15.6" 1080p); the SP7 panel is 12.3" 2736x1824 (267 dpi), where 160
  # makes text and the 10dp scrollbar overlay about 60 % of their intended size. 240 gives a 1824x1216dp desktop.
  'init_early':['setprop ro.hardware.camera gbrunch','setprop qemu.sf.lcd_density 240'],
  # surface/build-iptsd.sh output (iptsd v3.1.1, static Android x86_64; hashes printed by the script, reproducible).
  # bin/ is executed by the gbrunch.iptsd service (shell domain, rule execute_no_trans on vendor_file); etc/ and
  # share/iptsd are read as /oem/surface/etc/iptsd.conf and the device presets.
  'oem_surface':[
   {'file':'bin/iptsd','sha256':'d4a2c9913e4127cb404006505e06f31ffd1b3a45caab25fa83dbfe366ae13615','mode':'0755'},
   {'file':'bin/iptsd-check-device','sha256':'44a11ee66607e7e30040250ffacd1ae6d6653e3875a0262c55eab32bd96d61a4','mode':'0755'},
   {'file':'bin/tinymix','sha256':'dcbd096ee40f8d929fff05582483717f6019935bb6242d3135d8266a304b92d5','mode':'0755'},  # surface/build-tinymix.sh
   {'file':'licenses/tinyalsa-NOTICE.txt','sha256':'1401e1769d37424806c1f453124a7220b7fe544c82cdc68d3137c64451baf61e'},
   {'file':'etc/iptsd.conf','sha256':'c12573855d7ea300643f83e46950abc55874a4e74b6f382a4bc6f90ce6c23d9b','label':'vendor_configs_file'},
   {'file':'licenses/CLI11-LICENSE','sha256':'cfc76368aef8f51868fa1cda6bcfa26e583406a4cf8e48e725e7624df24d8855'},
   {'file':'licenses/GSL-LICENSE','sha256':'4c21dc82a1c7186ea754fd59d504c00947953a3401fe8e24eaa44109ef8e2555'},
   {'file':'licenses/README.txt','sha256':'9a3701bba308d2acedf67b96bbc58e47f8faf13f80adc45a1b140d76a53bab20'},
   {'file':'licenses/eigen-COPYING.APACHE','sha256':'03379001a7b12a2ec997a25554247d985270b353c10d5bafee9ac8d6519820b7'},
   {'file':'licenses/fmt-LICENSE','sha256':'07580f2a3b35709ce703d523f447b242f6dfec7582a8c0df102c7fa2849375f8'},
   {'file':'licenses/inih-LICENSE.txt','sha256':'9ae3b39e83e9158e44b67733baa3bb2d84e80efdcfb14a5820210a42c7abdf7f'},
   {'file':'licenses/iptsd-LICENSE.txt','sha256':'8177f97513213526df2cf6184d8ff986c675afb514d4e68a404010521b880643'},
   {'file':'licenses/spdlog-LICENSE','sha256':'d678c20826e1392578508e1c78649a1e3c31bda05ef4388d967dbd654461b140'},
   {'file':'share/iptsd/surface-book-1.conf','sha256':'0f4af10e49501a9d5fabde4015f603aea16c36d3aa0bb75ad8e5c8b9d96cefd2','label':'vendor_configs_file'},
   {'file':'share/iptsd/surface-book-2-13.conf','sha256':'a53631adec91f6780bfa424d67e1a8daef01431e4765699e296eb7578022f670','label':'vendor_configs_file'},
   {'file':'share/iptsd/surface-book-2-15.conf','sha256':'8887c26d5466c60bdc78a5e899455537054072ff145e921ee54b3e4f2ab8c758','label':'vendor_configs_file'},
   {'file':'share/iptsd/surface-laptop-1+2.conf','sha256':'845cc4d3a28f2ba39c6aee4e3cc873e96c640f7f1ae2e97be2e5a0d69d48d5aa','label':'vendor_configs_file'},
   {'file':'share/iptsd/surface-laptop-studio-2.conf','sha256':'90eefcebe5ff5b99adfc6e435f4bfa295f6bfa5e6e8dc30aa84503cd10b232f5','label':'vendor_configs_file'},
   {'file':'share/iptsd/surface-pro-4-a.conf','sha256':'b0402a324c4787ed1b6ae2a07a1ce9ccb8a2ada5f9ec64fdebe320adc1936a5d','label':'vendor_configs_file'},
   {'file':'share/iptsd/surface-pro-4-b.conf','sha256':'d3cb133912f4f6d704d9fd5a5d3641b33f24e5865bf26f5361e5dc414500297a','label':'vendor_configs_file'},
   {'file':'share/iptsd/surface-pro-4-c.conf','sha256':'61e18beb1b18e7ba5cdc5921238cb18af302a36b9cdb2462d3e81f2bd408651b','label':'vendor_configs_file'},
   {'file':'share/iptsd/surface-pro-5.conf','sha256':'86f65a8ddb230a5013585650e0948c2a083dcd815f5d14a4160d84ccd2d0a850','label':'vendor_configs_file'},
   {'file':'share/iptsd/surface-pro-6.conf','sha256':'b2fd2676f745cd8949f2dbd8a8bf2800a275ac89c2dde199505190afe8ae8bd8','label':'vendor_configs_file'},
  ],
  'policy_rules':[
   ('shell','hidraw_device','chr_file','open,read,write,ioctl,getattr'),
   ('shell','uhid_device','chr_file','open,read,write,ioctl,getattr'),
   ('shell','vendor_file','file','execute,execute_no_trans,open,read,getattr,map'),
   ('shell','vendor_file','dir','search,read,open,getattr'),
   ('shell','vendor_configs_file','file','open,read,getattr,map'),
   ('shell','kmsg_device','chr_file','open,write,getattr'),
   # gbrunch.audiodump (uid audio): the ALSA control node and /proc/asound for the mixer dump.
   ('shell','audio_device','chr_file','open,read,write,ioctl,getattr'),
   ('shell','audio_device','dir','search,read,open,getattr'),
   ('shell','proc_asound','file','open,read,getattr'),
   ('shell','proc_asound','dir','search,read,open,getattr'),
   # init binds the locally supplied IPU4P firmware over its /oem placeholder.
   ('init','vendor_firmware_file','file','mounton'),
  ],
  'init_post_fs_data':['start gbrunch.iptsd','start gbrunch.audiodump','mkdir /data/vendor/camera 0775 system camera'],
  # GBLOG snapshots for the touch stack (no shell on the device): kernel messages of ipts/iptsd, the hidraw and
  # uinput nodes with labels, the input device names (uinput "IPTS Touchscreen"/"IPTS Stylus" when iptsd runs),
  # the gbrunch.iptsd service state and SELinux denials of the shell domain.
  # live-kernel.txt: the last 512 KiB of the kernel log without the per-frame ISYS debug lines (SOF/EOF, buffer
  # queueing, capture responses), rewritten every 5 s from the first snapshot on, so events
  # between snapshots (camera stream errors) survive when GBLOG is collected right after a test. The loop ignores
  # SIGTERM/SIGHUP: on the device a background dmesg -w and then this loop were terminated about 30 s after boot.
  'logger_extra':'''    [ -n "$kfollow" ] || { ( trap "" TERM HUP; while :; do dmesg | grep -vE "sof_event|eof_event|sof: |eof: |buffer|queue buf|iova|output pin 0: buffer|index:|get buffer list|send_token: STREAM_CAPTURE$|FW resp .. (STREAM_CAPTURE_ACK|STREAM_CAPTURE_DONE|PIN_DATA_READY|FRAME_SOF|FRAME_EOF)" | tail -c 524288 > "$run/live-kernel.tmp" && mv "$run/live-kernel.tmp" "$run/live-kernel.txt"; sleep 5; done ) & kfollow=$!; echo "LIVE_KERNEL pid=$kfollow" >> "$status"; }
    capture "$n-camera.txt" sh -c 'dmesg | grep -iE "ipu|ov5693|ov8865|ov7251|dw9719|int3472|INT33BE|INT347|isys|csi" | grep -vE "sof_event|eof_event" | tail -160; echo "== csi2 sof/eof events: $(dmesg | grep -c sof_event) sof, $(dmesg | grep -c eof_event) eof; last:"; dmesg | grep -E "sof_event|eof_event" | tail -6; ls -lZ /oem/firmware/intel/ipu /vendor/firmware/intel/ipu /dev/video* /dev/media* /dev/v4l-subdev* 2>&1 | head -40; for v in /sys/class/video4linux/*; do echo "$v $(cat $v/name)"; done | head -20; dumpsys -t 5 media.camera | head -120; logcat -b main,system -d -v threadtime -s libcamera:V gbrunch-camera:V camera_hal_adapter_AidlCameraProvider:V CameraService:V CameraProviderManager:V | tail -c 262144'
    capture "$n-audio-controls.txt" sh -c 'logcat -d -s gbrunch-audiodump:V | tail -c 393216'
    capture "$n-touch.txt" sh -c 'dmesg | grep -iE "ipts|hidraw|uinput" | tail -60; echo "== gbrunch-iptsd (logcat)"; logcat -d -s gbrunch-iptsd:V | tail -40; echo "== /dev nodes"; ls -lZ /dev/hidraw* /dev/uinput 2>&1; echo "== input devices"; for d in /sys/class/input/input*; do echo "$d $(cat $d/name 2>/dev/null)"; done; echo "== service"; getprop init.svc.gbrunch.iptsd; ps -AZ | grep -E "iptsd|PID"; echo "== avc (kernel)"; dmesg | grep -E "avc:.*scontext=u:r:shell" | tail -40; echo "== avc (logcat)"; logcat -b events -d | grep -E "avc:.*scontext=u:r:shell" | tail -40; echo "== dumpsys input"; dumpsys -t 5 input | head -120'
''',
  'init_append':'''
service gbrunch.iptsd /system/bin/sh /oem/surface/iptsd.sh
    class late_start
    user system
    group system uhid input
    seclabel u:r:shell:s0
    disabled

service gbrunch.audiodump /system/bin/sh /oem/surface/audiodump.sh
    class late_start
    user audio
    group audio
    seclabel u:r:shell:s0
    disabled
    oneshot

on boot
    wait /sys/bus/i2c/drivers/ov5693/i2c-INT33BE:00 15
    wait /sys/bus/i2c/drivers/ov8865/i2c-INT347A:00 5
    restart vendor.camera-provider-2-7-legacy-wrapper
''',
  'vendor_files':[
   {'path':'lib64/hw/camera.gbrunch.so','camera_src':'vendor/camera.gbrunch.so',
    'sha256':'1b93099a7dab6a9cd4a583390930bfe8484219070c78d2a1d255610df502ae8e','template':'/lib64/hw/camera.libcamera_hal_base.so'},
   {'path':'firmware/intel/ipu/ipu4p_fw.bin','local':'intel/ipu/ipu4p_fw.bin','bytes':3960832,
    'sha256':'ff2c36cc81a5c726508b22970c2e2538ff06107dc5a72c93401403c227e5157f','template':'/firmware/intel/ipu/ipu6ep_fw.bin',
    'fetch':'run surface/fetch-ipu4p-firmware.sh WORK and pass --local-files WORK/out (Microsoft firmware, not redistributable)'},
   # DRAS fallback paths /vendor/etc/{audio_config,mixer_paths,volume_curves}.xml (absent in Ruby's vendor).
   {'path':'etc/audio_config.xml','src':'vendor/etc/audio_config.xml','template':'/etc/audio_policy_configuration.xml'},
   {'path':'etc/mixer_paths.xml','src':'vendor/etc/mixer_paths.xml','template':'/etc/audio_policy_configuration.xml'},
   {'path':'etc/volume_curves.xml','copy_from':'/etc/audio/sof-rt5682_alc5682ivs_tas2563/volume_curves.xml','template':'/etc/audio_policy_configuration.xml'},
   {'path':'overlay/GbrunchScrollbarOverlay.apk','src':'../hp-dragonfly-g4/vendor/overlay/GbrunchScrollbarOverlay.apk',
    'sha256':'a6c790663bdac1b22f4677cc074b0906e22dd51d9b2624b01c718b843926281e','template':'/overlay/framework-res__ruby__auto_generated_rro_vendor.apk'},
  ],
  # 'module' entries are expanded to their dependency closure from the kernel build's modules.dep.
  # early-init: ACPI platform devices (SAM on the LPSS UART, buttons). post-fs-data: PCI/USB devices and audio;
  # the HDA codecs and the machine driver are loaded before the SOF PCI driver because request_module() has no
  # usable modprobe here (Google's would look in vendor_dlkm).
  'oem_modules':[
   {'module':'surface_aggregator_registry','stage':'early-init'},
   {'module':'surface_aggregator_hub','stage':'early-init'},
   {'module':'surface_battery','stage':'early-init'},
   {'module':'surface_charger','stage':'early-init'},
   {'module':'surface_platform_profile','stage':'early-init'},
   {'module':'surface_gpe','stage':'early-init'},
   {'module':'surface_hotplug','stage':'early-init'},
   {'module':'soc_button_array','stage':'early-init'},
   {'module':'iwlmvm','stage':'post-fs-data'},
   {'module':'btusb','stage':'post-fs-data'},
   # The SOF HDA generic board always has dmic01/dmic16k links and waits for the "dmic-codec" component even with no
   # DMIC in the NHLT; without snd-soc-dmic the card stayed in "deferred probe pending" (device log 2026-11-10).
   {'module':'snd_soc_dmic','stage':'post-fs-data'},
   {'module':'snd_hda_codec_alc269','stage':'post-fs-data'},
   {'module':'snd_hda_codec_intelhdmi','stage':'post-fs-data'},
   {'module':'snd_soc_skl_hda_dsp','stage':'post-fs-data'},
   {'module':'snd_sof_pci_intel_icl','stage':'post-fs-data'},
   {'module':'sdhci_pci','stage':'post-fs-data'},
   {'module':'ipts','stage':'post-fs-data'},
   # Cameras: INT3472 (sensor power GPIOs), sensors and the focus motor, then the IPU (its ipu-bridge builds the
   # sensor graph from ACPI and requests the firmware at probe).
   {'module':'intel_skl_int3472_discrete','stage':'post-fs-data'},
   {'module':'ov5693','stage':'post-fs-data'},
   {'module':'ov8865','stage':'post-fs-data'},
   {'module':'dw9719','stage':'post-fs-data'},
   {'module':'intel_ipu6','stage':'post-fs-data'},
   # Add 'args':'dyndbg=+p' (kernel patch 0021) for the ISYS debug messages (stream setup, output pin format, D-PHY
   # timing, CSI-2 SOF/EOF per frame) in dmesg; the periodic live-kernel.txt leaves out the per-frame lines. They
   # found the rear camera's packed-RAW stall (kernel patch 0024).
   {'module':'intel_ipu6_isys','stage':'post-fs-data'},
   # USB Ethernet adapters (RTL8153 docks, ASIX, CDC): registered at boot so a later hot-plug binds; with Google's
   # kernel ueventd loaded these from vendor_dlkm by modalias, which cannot work for this kernel's modules.
   {'module':'r8152','stage':'post-fs-data'},
   {'module':'cdc_ether','stage':'post-fs-data'},
   {'module':'cdc_ncm','stage':'post-fs-data'},
   {'module':'ax88179_178a','stage':'post-fs-data'},
   {'module':'asix','stage':'post-fs-data'},
  ],
  # Cameras (IPU4P, kernel patches 0007-0020): the GBRUNCH libcamera stack (camera/build.sh, --camera WORK/out) with the
  # Surface configuration from profiles/surface-pro-7/camera (front OV5693, rear OV8865, CC0 tuning from the IPU4P work).
  # libcamera-base.so, libcamera.so, libcamera-hal.so, ipa_softisp.so from camera/build.sh with patches 0001-0007 (0002 ov5693/ov8865 sensor delays: steady
  # AGC; 0003 frames kept in the sensor's native orientation, which the HAL reports; 0004 flickerPeriod; 0005 log-levels file read by the logger; 0006 binned max width; 0007 full-width source).
  'oem_camera':[
   {'file':'lib64/libc++_shared.so','sha256':'39faf2d59346e65a18980f207794fab2f1faff8a8de3f087b8319a181fa16fbd'},
   {'file':'lib64/libcamera-base.so','sha256':'e6ea1e8fbe72d90e4e7a067dc68b6660e8efd714d816eab1de8261b56a458521'},
   {'file':'lib64/libcamera.so','sha256':'94f21f9530815b9a7f99a9197316823518ca78ed19c39fa05599f29bef217aa9'},
   {'file':'lib64/libcamera-hal.so','sha256':'064e9c866643b490f3fb947dfb067d624e65d416c13e2266cf7771f817db3017'},
   {'file':'lib64/libcamera/ipa/ipa_softisp.so','sha256':'f540374d6da97ff2906dd76074add18d2e6c8ff5ed005b7bb7cd1c3215c3a40f'},
   {'file':'lib64/share/libcamera/ipa/softisp/uncalibrated.yaml','sha256':'0689579ca79036bbd31d104deafca70fb8b267c4c8f8492e07bd6f6e875ee1ac'},
   {'file':'lib64/etc/libcamera/camera_hal.yaml','src':'camera/lib64/etc/libcamera/camera_hal.yaml','sha256':'9ae3b3d67bd5b2eb25b95af9b3ef51396e384104bc6184ab203278afaa0a20b3'},
   {'file':'lib64/etc/libcamera/log-levels','src':'camera/lib64/etc/libcamera/log-levels','sha256':'483780e5eabd52f57f829160308d4c3cf7312add9ba1f375ccd263bc1169ef62'},
   {'file':'lib64/share/libcamera/ipa/softisp/ov5693.yaml','src':'camera/lib64/share/libcamera/ipa/softisp/ov5693.yaml','sha256':'b7e0774e4aaa1c9191be3697f399844b48aab55e293d713b15ac63735787586c'},
   {'file':'lib64/share/libcamera/ipa/softisp/ov8865.yaml','src':'camera/lib64/share/libcamera/ipa/softisp/ov8865.yaml','sha256':'cc2e54022636983d51067a27ebdd61947d74696baf9ee646fb8d1f89d9d6d9ad'},
  ],
  # IPU4P firmware (Microsoft, not redistributable): GBVENDOR /vendor/firmware/intel/ipu/ipu4p_fw.bin from
  # assemble_native_android.py --local-files (surface/fetch-ipu4p-firmware.sh); init binds it over this empty
  # placeholder in firmware_class.path (/oem/firmware), where intel_ipu6 requests it at probe.
  'oem_placeholders':['firmware/intel/ipu/ipu4p_fw.bin'],
  'files':[
   {'name':'i915/icl_dmc_ver1_09.bin','target':'initrd','sha256':'aafad1967679baa36e971e5331da73eea31c68239416432df83e71332755dc01','bytes':25952},
   # AX201 (CNVi on Ice Lake, 8086:34f0): the Surface Pro 7 unit is a Qu C-step (rev 0x332: "iwlwifi-Qu-c0-hr-b0-77 is
   # required", device log 2026-11-09); the other 34f0 steppings ask for Qu-b0 or QuZ-a0. API 77 is the highest for this
   # RF and what the Qu MAC config accepts; no PNVM for HR.
   {'name':'iwlwifi-Qu-c0-hr-b0-77.ucode','target':'oem','sha256':'8044afa242ebbc68e84a64c235cb976288a3e0b3cecceeafc77c17fea0a86448','bytes':1406200},
   {'name':'iwlwifi-Qu-b0-hr-b0-77.ucode','target':'oem','sha256':'26d99bbda19a502d2052c5f885088a89576746522e60512b4c71f93900193300','bytes':1406184},
   {'name':'iwlwifi-QuZ-a0-hr-b0-77.ucode','target':'oem','sha256':'d4e84849de6d8b1f7c022278d8927d3e64f8e0e8713fbc9263b622d98b088dfd','bytes':1406320},
   # RTL8153 USB Ethernet firmware patches (r8152 works without them but logs a failed load).
   {'name':'rtl_nic/rtl8153a-3.fw','target':'oem','sha256':'c7099a1b0fc80e02ff7276aacbb08b5771e2f5c763fb76a7a8df3a89d58d8208','bytes':1440},
   {'name':'rtl_nic/rtl8153a-4.fw','target':'oem','sha256':'9cdafb4b6caa944c35b6ccb0ab7563ff377a36f38720b5b1eb35d90ecd756cd5','bytes':712},
   {'name':'rtl_nic/rtl8153b-2.fw','target':'oem','sha256':'137c9c752e8648ecfcf3b74ffc7d675c098145c222e2969721e6257af31716bc','bytes':1880},
   {'name':'LICENCE.rtlwifi_firmware.txt','target':'license','sha256':'a61351665b4f264f6c631364f85b907d8f8f41f8b369533ef4021765f9f3b62e','bytes':2115},
   # AX201 Bluetooth (USB 8087:0026): btintel asks for ibt-19-0-4 (hw_revision 0) or ibt-19-32-4 (a symlink to it upstream).
   {'name':'intel/ibt-19-0-4.sfi','target':'oem','sha256':'3ac36812eed6ef706a3444fbbacbba510d862eef7ac97b5987a62b07f5d88e7e','bytes':801224},
   {'name':'intel/ibt-19-0-4.ddc','target':'oem','sha256':'fe272982577efdc289cfe3e8bedafca607c0b7d6c9df1aab1880e22ef930d077','bytes':9},
   {'name':'intel/ibt-19-32-4.sfi','target':'oem','src':'intel/ibt-19-0-4.sfi','sha256':'3ac36812eed6ef706a3444fbbacbba510d862eef7ac97b5987a62b07f5d88e7e','bytes':801224},
   {'name':'intel/ibt-19-32-4.ddc','target':'oem','src':'intel/ibt-19-0-4.ddc','sha256':'fe272982577efdc289cfe3e8bedafca607c0b7d6c9df1aab1880e22ef930d077','bytes':9},
   # SOF for Ice Lake (IPC3): Intel-signed firmware and the HDA generic topologies.
   {'name':'intel/sof/sof-icl.ri','target':'oem','deb':'firmware-sof-signed','member':'usr/lib/firmware/intel/sof/intel-signed/sof-icl.ri','sha256':'ba5b9271d8266a09ceef17582bc3de7d690d788e87a4df690055ce4eaf37b2ed','bytes':557824},
   # The Pro 7's NHLT reports no DMICs (device log 2026-11-09), so the driver wants the plain sof-hda-generic.tplg.
   {'name':'intel/sof-tplg/sof-hda-generic.tplg','target':'oem','deb':'firmware-sof-signed','member':'usr/lib/firmware/intel/sof-tplg/sof-hda-generic.tplg','sha256':'db62fb43dee542a57cf20c15df5724fc7c446e320924db3bb4f06ab1c688f00f','bytes':31395},
   {'name':'intel/sof-tplg/sof-hda-generic-2ch.tplg','target':'oem','deb':'firmware-sof-signed','member':'usr/lib/firmware/intel/sof-tplg/sof-hda-generic-2ch.tplg','sha256':'7896ab52853e64d063f5e897011d200c2944732d75a96787667454fe2f443c53','bytes':44247},
   {'name':'intel/sof-tplg/sof-hda-generic-4ch.tplg','target':'oem','deb':'firmware-sof-signed','member':'usr/lib/firmware/intel/sof-tplg/sof-hda-generic-4ch.tplg','sha256':'7b1b185b35f1034ca1957f41e75c184f79ea1cae7b38bcef995acf7481145d20','bytes':44383},
   {'name':'firmware-sof-signed.copyright','target':'license','deb':'firmware-sof-signed','member':'usr/share/doc/firmware-sof-signed/copyright','sha256':'f769db7d9007c9e2c6aaa4076fc6604e771c88c685c2fda4045e0d0ab11ffc06','bytes':109946},
   {'name':'LICENSE.i915','target':'license','sha256':'8542aeabf2761935122d693561e16766ce1bcc2b0d003204f9040b7d6d929f2e','bytes':2080},
   {'name':'LICENCE.iwlwifi_firmware','target':'license','sha256':'16d5040c7cf851fc693b7542e20870935b99802533ea1bfd231c377a2305e5c5','bytes':2046},
   {'name':'LICENCE.ibt_firmware','target':'license','sha256':'5181b0b51efc79d5acb2c9bb92042878fdbad97a92114d4ab5e32e2b5b52fce4','bytes':2040},
  ],
 },
 'hp-dragonfly-g4':{
  'board':'8B63',
  'compat_sha256':'f2478ea67705ea3f1f3dcbfd07e1dec0cf7f677ab9f126114d6bdec01a64a4f7',
  # Physical test 2026-10-31: with defaults the RPL-U eDP primary plane faulted
  # ("[PLANE:33:plane 1A] fault", Y-tiled XRGB via DPT) and the panel went black.
  # These xe display options removed every fault; setup and apps then worked.
  # snd_intel_dspcfg.dsp_driver=3: force SOF. Otherwise snd_soc_avs claims 8086:51ca, finds no
  # intel/avs/adl/dsp_basefw.bin and leaves "No soundcards found" (physical test 2026-10-31).
  # Ruby's vendor_dlkm modules.options sets "snd_sof_intel_hda_generic hda_model=alc-2024y-chromebook
  # dmic_num=4". libmodprobe appends kernel-cmdline module options, so these override it: 2 DMICs (NHLT),
  # and an unknown model name so the ALC245 falls back to its 103c:8b63 SSID quirk.
  'cmdline_append':'xe.enable_psr=0 xe.enable_dpt=0 xe.enable_fbc=0 snd_intel_dspcfg.dsp_driver=3 '
                   'snd_sof_intel_hda_generic.dmic_num=2 snd_sof_intel_hda_generic.hda_model=auto',
  'logger_capture':True,
  # Owned files under profiles/<name>/oem, written to /oem with the given SELinux label, and
  # file bind mounts added to init.gbrunch.rc early-init (init may mount on vendor_configs_file files).
  'oem_files':[('audio/hal_audio.xml','vendor_configs_file'),('audio/audio_config.xml','vendor_configs_file'),
               ('audio/mixer_paths.xml','vendor_configs_file')],
  # Files added to the locally derived GBVENDOR (vendor.erofs) by the tool VM. DRAS cannot resolve a
  # SKU/model on non-Google hardware (no ro.boot.product.hardware.id), so it reads its fallback paths
  # /vendor/etc/{audio_config,mixer_paths,volume_curves}.xml, which do not exist in Ruby's vendor.
  # 'src' is an owned profile file; 'copy_from' copies a file of the local recovery inside the VM,
  # so no Google bytes enter the public kit. 'template' supplies mode/owner/SELinux xattrs.
  'vendor_files':[
   {'path':'etc/audio_config.xml','src':'vendor/etc/audio_config.xml','template':'/etc/audio_policy_configuration.xml'},
   {'path':'etc/mixer_paths.xml','src':'vendor/etc/mixer_paths.xml','template':'/etc/audio_policy_configuration.xml'},
   {'path':'etc/volume_curves.xml','copy_from':'/etc/audio/sof-rt5682_alc5682ivs_tas2563/volume_curves.xml','template':'/etc/audio_policy_configuration.xml'},
   # Camera HAL module for ro.hardware.camera=gbrunch: it loads the libcamera HAL from /vendor/lib64/libcamera,
   # which GBRUNCH bind-mounts from /oem/camera/lib64, so HAL updates need only GBRUNCH.
   {'path':'lib64/hw/camera.gbrunch.so','camera_src':'vendor/camera.gbrunch.so',
    'sha256':'1b93099a7dab6a9cd4a583390930bfe8484219070c78d2a1d255610df502ae8e','template':'/lib64/hw/camera.libcamera_hal_base.so'},
   # Static RRO on framework-res: config_scrollbarSize 4dp -> 10dp, so View scrollbars (Settings, Files...)
   # are visible and easy to grab with a mouse. Built reproducibly by overlays/scrollbar/build.sh
   # (AOSP public test key); PackageManager picks up static overlays in /vendor/overlay by itself.
   {'path':'overlay/GbrunchScrollbarOverlay.apk','src':'vendor/overlay/GbrunchScrollbarOverlay.apk',
    'sha256':'a6c790663bdac1b22f4677cc074b0906e22dd51d9b2624b01c718b843926281e','template':'/overlay/framework-res__ruby__auto_generated_rro_vendor.apk'},
  ],
  # Out-of-tree GKI modules from tools/build_gki_modules.py (--gki-modules DIR), loaded by init.
  # ac.ko: ACPI0003 AC adapter. Ruby's vendor modules ship battery.ko but not ac.ko, so Android
  # never saw the charger (AC powered: false).
  # 'stage': early-init (ACPI platform devices exist) or post-fs-data (after ueventd coldboot has loaded the
  # LPSS SPI controller). Entries with 'stock' are Ruby vendor modules, loaded in place from /vendor.
  # Speakers: 4x CS35L41 on SPI0 behind ACPI CSC3551. The chain is stock cs_dsp/cs-amp-lib, then our
  # cs35l41-lib (it replaces the stock one, whose CRCs come from a different tree; never load both), then
  # the gbrunch-csc3551-spi shim (creates and names the SPI devices), then the CS35L41 HDA driver + SPI glue.
  # system_heap: /dev/dma_heap/system. Ruby loads it from init.insmod.rc, which the compat image
  # excludes; without it the USB camera HAL fails V4L2 buffer allocation (black preview).
  'oem_modules':[
   {'file':'ac.ko','sha256':'773b704764e4b45ad853630c986634a54146ddcd65fbe5a0009ea6c421ffe680','stage':'early-init'},
   {'stock':'/vendor/lib/modules/system_heap.ko','stage':'early-init'},
   {'stock':'/vendor/lib/modules/cs_dsp.ko','stage':'post-fs-data'},
   {'stock':'/vendor/lib/modules/snd-soc-cs-amp-lib.ko','stage':'post-fs-data'},
   {'file':'snd-soc-cs35l41-lib.ko','sha256':'e6fa3f7048e07641a68efc4a497280dcef83f0738f8a482f539e8250ae6f6712','stage':'post-fs-data'},
   {'file':'gbrunch_csc3551_spi.ko','sha256':'78b2f6b1af927176ed74e404b16bc3d120638124060e9c826f0c93a1e17233d9','stage':'post-fs-data'},
   {'file':'snd-hda-scodec-cs35l41.ko','sha256':'d31627c356db31814b1f6d15c223df610165db8e45eef28259fcd14beb391ab7','stage':'post-fs-data'},
   {'file':'snd-hda-scodec-cs35l41-spi.ko','sha256':'d1c1c194fb96482ac067db41536319aa70f99688a477f20b4ff7ddfdb5513c71','stage':'post-fs-data'},
   # Internal camera: upstream ipu-bridge, then the shim that builds the sensor graph for the stock IPU6
   # driver (INT3472 has bound by post-fs-data). init rebinds intel-ipu6 on boot so it uses the graph.
   {'file':'ipu-bridge.ko','sha256':'08d84420ae0d6fc05bd52ea2b467feb8a4eb2cc217fd66718b799f78a154b71f','stage':'post-fs-data'},
   {'file':'gbrunch_ipu6_bridge.ko','sha256':'5b093f922d5a73ab7535ba0437f66b30b9ea81ed67b286ec3c84bad9eb7a882a','stage':'post-fs-data'},
  ],
  # Internal camera: upstream libcamera (simple pipeline + software ISP + Android HAL), camera/build.sh
  # (--camera WORK/out). lib64/ is bind-mounted over /vendor/lib64/libcamera, where GBVENDOR's camera.gbrunch.so
  # loads libcamera-hal.so. bin/gbcam-probe is a manual capture test (logcat tag gbcam), not started by init.
  # Pinned to camera/build.sh with patch 0001 only (the build before 0002/0003 were added); a rebuild with the
  # current patches changes libcamera.so and libcamera-hal.so; re-pin and re-test the hi556 camera at the next Dragonfly build.
  'oem_camera':[
   {'file':'lib64/libc++_shared.so','sha256':'39faf2d59346e65a18980f207794fab2f1faff8a8de3f087b8319a181fa16fbd'},
   {'file':'lib64/libcamera-base.so','sha256':'0953d1e01293cdea274a3072f7afe9cfcc3f15c9a962b1c504b43c1799f2d66d'},
   {'file':'lib64/libcamera.so','sha256':'c5c6232a1d378d821d04e5d2552f769c74b70d2b9ce11026772a013bd454137b'},
   {'file':'lib64/libcamera-hal.so','sha256':'268f8a18ddee1c5c6248ceaf39bf67e69f03a4d09102b3cb9383a2c9aa4745f7'},
   {'file':'lib64/libcamera/ipa/ipa_softisp.so','sha256':'c56d5d44d1210af6070f8c6d261a847c69f1b4b291b22e3cd0a83d4d45afb40e'},
   {'file':'lib64/share/libcamera/ipa/softisp/uncalibrated.yaml','sha256':'0689579ca79036bbd31d104deafca70fb8b267c4c8f8492e07bd6f6e875ee1ac'},
   {'file':'lib64/share/libcamera/ipa/softisp/hi556.yaml','sha256':'73eb25a6020fe76e2502c39a42dd166417b1b8c4c7c3c185ee30478f8e7846e0'},
   {'file':'lib64/etc/libcamera/camera_hal.yaml','sha256':'a049647d62d3f92fa346a5058b55bb4a504af04cd28f337af323c2b6734de428'},
   {'file':'bin/gbcam-probe','sha256':'601005b7c8e1411b2803261f19a44eb34588f0f0e639bc4c69e74423629c5147',
    'label':'hal_camera_default_exec','mode':'0755'},
  ],
  # Text appended to init.gbrunch.rc.
  # Ruby's intel-ipu6.ko has no ipu-bridge, so hi556 waits for its fwnode endpoint. After
  # gbrunch_ipu6_bridge.ko (post-fs-data) has set the sensor graph, rebind the stock driver to use it.
  # The camera provider (class hal) starts before the sensor is bound, and libcamera without udev does
  # not see later cameras, so restart it once hi556 has probed after the rebind.
  'init_append':'''
on boot
    write /sys/bus/pci/drivers/intel-ipu6/unbind 0000:00:05.0
    write /sys/bus/pci/drivers/intel-ipu6/bind 0000:00:05.0
    wait /sys/bus/i2c/drivers/hi556/i2c-INT3537:00 10
    restart vendor.camera-provider-2-7-legacy-wrapper
''',
  # Extra post-fs-data commands. /data/vendor/camera is created by Ruby's excluded init.common.rc;
  # the camera effects pipeline caches there.
  'init_post_fs_data':['mkdir /data/vendor/camera 0775 system camera'],
  'init_binds':[('/oem/audio/hal_audio.xml','/vendor/etc/data/hal_configs/audio_default.xml'),
                ('/oem/audio/hal_audio.xml','/vendor/etc/data/hal_configs/audio_alc5682i_tas2563rppr.xml'),
                ('/oem/audio/audio_config.xml','/vendor/etc/audio/sof-rt5682_alc5682ivs_tas2563/audio_config.xml'),
                ('/oem/audio/mixer_paths.xml','/vendor/etc/audio/sof-rt5682_alc5682ivs_tas2563/mixer_paths.xml'),
                # DRAS's fallback path (vendor_files below); bound so mixer changes need only GBRUNCH.
                ('/oem/audio/mixer_paths.xml','/vendor/etc/mixer_paths.xml'),
                # Camera stack (directory bind; Ruby's own libcamera plugins there are unused).
                ('/oem/camera/lib64','/vendor/lib64/libcamera')],
  # Extra early-init commands, after the binds. Ruby sets this in the excluded init.fatcat.rc (to its own
  # libcamera_hal_base); the legacy camera provider then loads /vendor/lib64/hw/camera.gbrunch.so.
  'init_early':['setprop ro.hardware.camera gbrunch'],
  'evidence':'Ubuntu 7.0 live dmesg on the target: i915 adlp_dmc/adlp_guc_70/tgl_huc, iwlwifi so-a0-gf-a0-89 (8086:51f1 sub 8086:0094), btintel ibt-0040-0041 (8087:0033)',
  'files':[
   {'name':'i915/adlp_dmc.bin','target':'initrd','sha256':'3516de2e134ddcf3b319c75d2e437779fecbd58cbb77234bd6f297c544e92ccb','bytes':79088},
   {'name':'i915/adlp_guc_70.bin','target':'initrd','sha256':'451ee39d1dcd0c8169dfb6af7b585e4873e8bf5ca72e7625e4d79b9cc66c2f4c','bytes':341696},
   {'name':'i915/tgl_huc.bin','target':'initrd','sha256':'dbb1316bac13a76427ffa7827e52b5b9affe8a4cadd23dc22e2a54e35c53bdad','bytes':589888},
   # Internal camera (IPU6EP 8086:a75d). Ruby's vendor has its own build, but GBVENDOR's /firmware/intel/ipu is empty.
   {'name':'intel/ipu/ipu6ep_fw.bin','target':'oem','sha256':'60105304e5b66a5a85aac7541b31dc71740240c6dc1a144036b4b72ef2fb1ba1','bytes':466944},
   {'name':'iwlwifi-so-a0-gf-a0-89.ucode','target':'oem','sha256':'73171b7b1e0ded4687772cf7faac1e87d7ebbb193704361542f1eac73086f401','bytes':1735944},
   {'name':'iwlwifi-so-a0-gf-a0.pnvm','target':'oem','sha256':'99b9fb2c88e4e9dbd0559a768190affc78846897764f7c9af21d29fd65ff69b7','bytes':55208},
   {'name':'intel/ibt-0040-0041.sfi','target':'oem','sha256':'e4d46a2d9c0fe973c1a62a037c9a792df9b8aa2f5bf072eedd63b2c03ef3fdf9','bytes':719012},
   {'name':'intel/ibt-0040-0041.ddc','target':'oem','sha256':'fe272982577efdc289cfe3e8bedafca607c0b7d6c9df1aab1880e22ef930d077','bytes':9},
   # SOF for RPL-P (8086:51ca), HDA codec ALC245 + 2 DMICs. Both IPC3 and IPC4 sets, since the
   # GKI default IPC for RPL is not established; Ubuntu 7.0 used IPC3 with the -2ch topology.
   {'name':'intel/sof/sof-rpl.ri','target':'oem','deb':'firmware-sof-signed','member':'usr/lib/firmware/intel/sof/intel-signed/sof-adl.ri','sha256':'294e5a51b1d3fc4ceda57780927c2a2ec95bb5d23477f879a9fb648c6c01cb30','bytes':525056},
   {'name':'intel/sof-tplg/sof-hda-generic-2ch.tplg','target':'oem','deb':'firmware-sof-signed','member':'usr/lib/firmware/intel/sof-tplg/sof-hda-generic-2ch.tplg','sha256':'7896ab52853e64d063f5e897011d200c2944732d75a96787667454fe2f443c53','bytes':44247},
   # 4ch topologies too, in case the dmic_num override is not applied (Ruby default requests -4ch).
   {'name':'intel/sof-tplg/sof-hda-generic-4ch.tplg','target':'oem','deb':'firmware-sof-signed','member':'usr/lib/firmware/intel/sof-tplg/sof-hda-generic-4ch.tplg','sha256':'7b1b185b35f1034ca1957f41e75c184f79ea1cae7b38bcef995acf7481145d20','bytes':44383},
   {'name':'intel/sof-ipc4-tplg/sof-hda-generic-4ch.tplg','target':'oem','deb':'firmware-sof-signed','member':'usr/lib/firmware/intel/sof-ipc4-tplg/sof-hda-generic-4ch.tplg','sha256':'381712d6b22d0bcdabc1d84ac5be083efe874b71691484211b1d22bc56a54ebe','bytes':60366},
   {'name':'intel/sof-ipc4/rpl/sof-rpl.ri','target':'oem','deb':'firmware-sof-signed','member':'usr/lib/firmware/intel/sof-ipc4/rpl/intel-signed/sof-rpl.ri','sha256':'351325786e53ca7cd23ffc1462d82913f3e0c08ad78f89053d85e4b6703cf12b','bytes':707036},
   {'name':'intel/sof-ipc4-tplg/sof-hda-generic-2ch.tplg','target':'oem','deb':'firmware-sof-signed','member':'usr/lib/firmware/intel/sof-ipc4-tplg/sof-hda-generic-2ch.tplg','sha256':'6d1313cf7311dcc47ccb78a05841f25da9640c4e87cb04a346ddf56f43f13f7b','bytes':60982},
   {'name':'firmware-sof-signed.copyright','target':'license','deb':'firmware-sof-signed','member':'usr/share/doc/firmware-sof-signed/copyright','sha256':'f769db7d9007c9e2c6aaa4076fc6604e771c88c685c2fda4045e0d0ab11ffc06','bytes':109946},
   # CS35L41 speaker-protection firmware, as loaded by Ubuntu on this unit (SSID 103C8B63, 4 amps).
   {'name':'cirrus/cs35l41-dsp1-spk-prot-103c8b63.wmfw','src':'cirrus/cs35l41/v6.61.1/halo_cspl_RAM_revB2_29.63.1.wmfw','target':'oem','sha256':'c11cb3e25825c673a955a3519370ad77fbcd8bcf68808e8e34140a70caefcec4','bytes':34068},
   {'name':'cirrus/cs35l41-dsp1-spk-prot-103c8b63-l0.bin','target':'oem','sha256':'273abb7b27ff930b29903831b9e3961849f0478e4dada14e1b06aa7f0713d5bc','bytes':5408},
   {'name':'cirrus/cs35l41-dsp1-spk-prot-103c8b63-r0.bin','target':'oem','sha256':'07cc98f864fc0a4df842b00daf9bf640ac0d118a3902c05f934b2d0db0389c3d','bytes':5408},
   {'name':'cirrus/cs35l41-dsp1-spk-prot-103c8b63-l1.bin','target':'oem','sha256':'14d9db516818695bc6e88b40c9a4a4b53e213b6967ea71f524070bce5b868681','bytes':5448},
   {'name':'cirrus/cs35l41-dsp1-spk-prot-103c8b63-r1.bin','target':'oem','sha256':'1c98637a6e347383db3051263933b8e9c143e0a168b40e6e5595f39256bae1f6','bytes':5448},
   {'name':'LICENSE.cirrus','target':'license','sha256':'ff6e5ab98d3e3c50d9e750580059bc23a0663a5613659828d0536db44c38f4e0','bytes':26746},
   {'name':'LICENSE.i915','target':'license','sha256':'8542aeabf2761935122d693561e16766ce1bcc2b0d003204f9040b7d6d929f2e','bytes':2080},
   {'name':'LICENCE.iwlwifi_firmware','target':'license','sha256':'16d5040c7cf851fc693b7542e20870935b99802533ea1bfd231c377a2305e5c5','bytes':2046},
   {'name':'LICENCE.ibt_firmware','target':'license','sha256':'5181b0b51efc79d5acb2c9bb92042878fdbad97a92114d4ab5e32e2b5b52fce4','bytes':2040},
  ]},
}

ENV=dict(os.environ,E2FSPROGS_FAKE_TIME=FAKE_TIME)

def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()

def deb_member(entry,cache):
    pkg=DEBS[entry['deb']];deb=cache/'debs'/pkg['url'].rsplit('/',1)[1]
    if not deb.exists():
        deb.parent.mkdir(parents=True,exist_ok=True);part=deb.with_name(deb.name+'.download')
        print('Downloading',pkg['url'],flush=True)
        with urllib.request.urlopen(pkg['url'],timeout=120) as r:part.write_bytes(r.read())
        part.rename(deb)
    if sha(deb)!=pkg['sha256']:raise ValueError('Pinned package mismatch: '+str(deb))
    tar=subprocess.run(['dpkg-deb','--fsys-tarfile',str(deb)],check=True,capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        m=t.getmember('./'+entry['member'])
        if not m.isfile():raise ValueError('Package member is not a regular file: '+entry['member'])
        return t.extractfile(m).read()

def fetch(entry,cache):
    if 'deb' in entry:
        path=cache/'from-deb'/entry['deb']/entry['name']
        if not path.exists():
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(deb_member(entry,cache))
    else:
        path=cache/COMMIT/entry.get('src',entry['name'])  # 'src': linux-firmware link target
    if not path.exists():
        path.parent.mkdir(parents=True,exist_ok=True);part=path.with_name(path.name+'.download')
        url=SOURCE+entry.get('src',entry['name']);print('Downloading',url,flush=True)
        with urllib.request.urlopen(url,timeout=60) as r:part.write_bytes(r.read())
        part.rename(path)
    if path.stat().st_size!=entry['bytes'] or sha(path)!=entry['sha256']:
        raise ValueError('Pinned firmware mismatch: '+str(path))
    return path

def debugfs(image,*commands):
    with tempfile.NamedTemporaryFile('w',suffix='.cmd',delete=False) as f:
        f.write('\n'.join(commands)+'\n');script=f.name
    try:
        out=subprocess.run(['debugfs','-w','-f',script,str(image)],check=True,capture_output=True,text=True,env=ENV)
    finally:pathlib.Path(script).unlink()
    bad=[l for l in out.stderr.splitlines() if l.strip() and not l.startswith('debugfs ')]
    if bad:raise RuntimeError('debugfs: '+'\n'.join(bad))

def exists_in(image,path):
    out=subprocess.run(['debugfs','-R','stat '+path,str(image)],capture_output=True,text=True,env=ENV)
    return 'Inode:' in out.stdout

def add_oem_firmware(image,entries):
    for e,src in entries:
        dest='/firmware/'+e['name']
        parts=dest.split('/')[1:-1]
        for i in range(2,len(parts)+1):  # /firmware itself exists; create missing descendants top-down
            parent='/'+'/'.join(parts[:i])
            if not exists_in(image,parent):
                debugfs(image,'mkdir '+parent,'sif %s mode 040755'%parent,'sif %s uid 0'%parent,'sif %s gid 0'%parent,
                        'ea_set %s security.selinux %s'%(parent,FIRMWARE_LABEL))
        if exists_in(image,dest):raise ValueError('Refusing to replace existing '+dest)
        debugfs(image,'write %s %s'%(src,dest),'sif %s mode 0100644'%dest,'sif %s uid 0'%dest,'sif %s gid 0'%dest,
                'ea_set %s security.selinux %s'%(dest,FIRMWARE_LABEL))
    subprocess.run(['e2fsck','-fn',str(image)],check=True,capture_output=True,env=ENV)

# Patch for the tool VM's payload/native_vendor_in_vm.py (V7 initrd version): add profile files from
# /payload/extra-vendor.json to the vendor overlay, taking metadata from an existing template path.
VM_PATCHES=[
 ("""RULES += GRAPHICS_RULES
""","""RULES += GRAPHICS_RULES
# Profile rules (prepare_profile_assets.py 'policy_rules'): the same source,target,class,perms quadruples.
if pathlib.Path('/payload/extra-rules.json').exists():
    for rule in json.loads(pathlib.Path('/payload/extra-rules.json').read_text()):RULES+=list(rule)
"""),
 ("""    for n in TOOLS:
""","""    extra=json.loads(pathlib.Path('/payload/extra-vendor.json').read_text()) if pathlib.Path('/payload/extra-vendor.json').exists() else []
    template={}
    for e in extra:
        if 'local' in e:
            # Not redistributable: supplied at assembly time (assemble_native_android.py --local-files).
            lp=pathlib.Path('/payload/local')/e['local']
            if not lp.exists():print('LOCAL_FILE_MISSING',e['local'],'(not added to vendor)');continue
            data=lp.read_bytes();assert hashlib.sha256(data).hexdigest()==e['sha256'],e['path']
        else:
            data=extract(e['copy_from']) if 'copy_from' in e else __import__('base64').b64decode(e['data'])
            assert 'copy_from' in e or hashlib.sha256(data).hexdigest()==e['sha256'],e['path']
        overlays[e['path']]=data;template[e['path']]=e['template']
    for n in TOOLS:
"""),
 ("""            m=meta(SOURCE,'/'+n,OFFSET);ti=tarfile.TarInfo(n);ti.size=len(b)""",
  """            m=meta(SOURCE,template.get(n,'/'+n),OFFSET);ti=tarfile.TarInfo(n);ti.size=len(b)"""),
]

def cpio_newc(entries):
    out=b''
    for i,(name,mode,data) in enumerate(entries+[('TRAILER!!!',0,b'')]):
        n=name.encode()+b'\0';fields=[880000+i,mode,0,0,1,0,len(data),0,0,0,0,len(n),0]
        h=b'070701'+b''.join(b'%08x'%v for v in fields)+n
        out+=h+bytes(-len(h)%4)+data+bytes(-len(data)%4)
    return out

def cpio_last(raw,want):
    pos=0;found=None
    while pos+110<=len(raw):
        if raw[pos:pos+6]!=b'070701':pos+=1;continue  # archive padding between members
        f=[int(raw[pos+6+i*8:pos+14+i*8],16) for i in range(13)];size,ns=f[6],f[11]
        name=raw[pos+110:pos+110+ns-1].decode();h=(110+ns+3)&~3
        if name==want:found=raw[pos+h:pos+h+size]
        pos+=h+((size+3)&~3)
    if found is None:raise ValueError('initrd lacks '+want)
    return found

def patch_tool_initrd(path,vendor_files,profile_dir,camera_dir=None,no_stock_modules=False,policy_rules=()):
    # Append a second gzip'd cpio member; the kernel unpacks it after the original and its files win.
    raw=gzip.decompress(path.read_bytes())
    # The initrd holds several concatenated cpio archives and the kernel keeps the LAST copy of a path.
    # (cpio -i stops at the first TRAILER, which is an older script without the graphics/shell rules.)
    script=cpio_last(raw,'payload/native_vendor_in_vm.py').decode()
    for old,new in VM_PATCHES:
        if script.count(old)!=1:raise ValueError('tool VM script patch anchor not found: '+old[:40])
        script=script.replace(old,new)
    extra=[]
    for e in vendor_files:
        d={'path':e['path'],'template':e['template']}
        if 'local' in e:
            d['local']=e['local'];d['sha256']=e['sha256'];extra.append(d);continue
        if 'src' in e or 'camera_src' in e:
            if 'camera_src' in e and not camera_dir:raise ValueError('vendor file %s needs --camera'%e['path'])
            data=(profile_dir/e['src'] if 'src' in e else camera_dir/e['camera_src']).read_bytes()
            if 'sha256' in e and hashlib.sha256(data).hexdigest()!=e['sha256']:raise ValueError('vendor file hash mismatch: '+e.get('src',e.get('camera_src')))
            d['data']=base64.b64encode(data).decode();d['sha256']=hashlib.sha256(data).hexdigest()
        else:d['copy_from']=e['copy_from']
        extra.append(d)
    entries=[('payload/native_vendor_in_vm.py',0o100755,script.encode()),
             ('payload/extra-vendor.json',0o100644,(json.dumps(extra,indent=1)+'\n').encode())]
    # With a replacement kernel the first-stage modules come from the kit, so the VM extracts none.
    if no_stock_modules:entries.append(('payload/modules.json',0o100644,b'[]\n'))
    for r in policy_rules:
        if len(r)!=4 or not all(re.fullmatch(r'[a-z0-9_,]+',x) for x in r):raise ValueError('bad policy rule %r'%(r,))
    if policy_rules:entries.append(('payload/extra-rules.json',0o100644,(json.dumps([list(r) for r in policy_rules],indent=1)+'\n').encode()))
    member=cpio_newc(entries)
    with path.open('ab') as f:f.write(gzip.compress(member,mtime=0))

def add_oem_file(image,src,dest,label,mode='0644'):
    if mode not in ('0644','0755'):raise ValueError('Unsupported mode '+mode)
    if not re.fullmatch(r'[a-z0-9_]+',label) or not re.fullmatch(r'/[A-Za-z0-9_.+/-]+',dest):raise ValueError('Unsafe oem file '+dest)
    if exists_in(image,dest):raise ValueError('Refusing to replace existing '+dest)
    parts=dest.split('/')[1:-1]
    for i in range(1,len(parts)+1):
        d='/'+'/'.join(parts[:i])
        if not exists_in(image,d):
            debugfs(image,'mkdir '+d,'sif %s mode 040755'%d,'sif %s uid 0'%d,'sif %s gid 0'%d,  # never an exec type
                    'ea_set %s security.selinux u:object_r:%s:s0'%(d,'vendor_file' if label.endswith('_exec') else label))
    debugfs(image,'write %s %s'%(src,dest),'sif %s mode 0100%s'%(dest,mode[1:]),'sif %s uid 0'%dest,'sif %s gid 0'%dest,
            'ea_set %s security.selinux u:object_r:%s:s0'%(dest,label))

def replace_oem_text(image,path,old,new):
    # Rewrite one owned text file in compat.ext4, keeping mode, owner and SELinux label.
    with tempfile.TemporaryDirectory() as td:
        cur=pathlib.Path(td)/'cur';subprocess.run(['debugfs','-R','dump %s %s'%(path,cur),str(image)],check=True,capture_output=True,env=ENV)
        text=cur.read_text()
        if text.count(old)!=1:raise ValueError('%s: anchor not found exactly once'%path)
        ea=subprocess.run(['debugfs','-R','ea_get -V %s security.selinux'%path,str(image)],check=True,capture_output=True,text=True,env=ENV).stdout.strip()
        if not re.fullmatch(r'u:object_r:[a-z0-9_]+:s0',ea):raise ValueError('%s: unexpected label %r'%(path,ea))
        upd=pathlib.Path(td)/'new';upd.write_text(text.replace(old,new))
        debugfs(image,'rm '+path,'write %s %s'%(upd,path),'sif %s mode 0100644'%path,'sif %s uid 0'%path,'sif %s gid 0'%path,
                'ea_set %s security.selinux %s'%(path,ea))
    subprocess.run(['e2fsck','-fn',str(image)],check=True,capture_output=True,env=ENV)

def strip_oem_lines(image,path,pattern):
    # Remove every line of an owned text file in compat.ext4 that matches pattern (a regex on the whole line).
    with tempfile.TemporaryDirectory() as td:
        cur=pathlib.Path(td)/'cur';subprocess.run(['debugfs','-R','dump %s %s'%(path,cur),str(image)],check=True,capture_output=True,env=ENV)
        lines=cur.read_text().splitlines(keepends=True)
        kept=[l for l in lines if not re.fullmatch(pattern,l.rstrip('\n'))]
        removed=[l.strip() for l in lines if re.fullmatch(pattern,l.rstrip('\n'))]
        if not removed:raise ValueError('%s: no line matches %s'%(path,pattern))
        ea=subprocess.run(['debugfs','-R','ea_get -V %s security.selinux'%path,str(image)],check=True,capture_output=True,text=True,env=ENV).stdout.strip()
        if not re.fullmatch(r'u:object_r:[a-z0-9_]+:s0',ea):raise ValueError('%s: unexpected label %r'%(path,ea))
        upd=pathlib.Path(td)/'new';upd.write_text(''.join(kept))
        debugfs(image,'rm '+path,'write %s %s'%(upd,path),'sif %s mode 0100644'%path,'sif %s uid 0'%path,'sif %s gid 0'%path,
                'ea_set %s security.selinux %s'%(path,ea))
    subprocess.run(['e2fsck','-fn',str(image)],check=True,capture_output=True,env=ENV)
    return removed

def kernel_assets(kdir,tmp,manifest,profile_name):
    """Copy a tools/build_profile_kernel.py output into the asset directory; returns (spec, modules root)."""
    spec=json.loads((kdir/'kernel.json').read_text())
    if spec.get('profile')!=profile_name:raise ValueError('kernel build is for profile %r, not %r'%(spec.get('profile'),profile_name))
    if 'kernel_sha256' not in spec:raise ValueError('kernel build was not compiled (no kernel_sha256 in kernel.json)')
    if sha(kdir/'kernel')!=spec['kernel_sha256']:raise ValueError('kernel image does not match kernel.json')
    for n in ['kernel','kernel.json','kernel.config']:shutil.copyfile(kdir/n,tmp/n)
    (tmp/'modules').mkdir()
    for m in spec['ramdisk_modules']:
        src=kdir/'ramdisk-modules'/m['file']
        if sha(src)!=m['sha256']:raise ValueError('ramdisk module hash mismatch: '+m['file'])
        shutil.copyfile(src,tmp/'modules'/m['file'])
    manifest['modules']=[{k:m[k] for k in ['name','file','sha256','depends','aliases']} for m in spec['ramdisk_modules']]
    manifest['replacement_kernel']={'file':'kernel','sha256':spec['kernel_sha256'],'release':spec['release'],'profile':profile_name,
                                    'source_commit':spec['source_commit'],'patches':spec['patches'],'fragments':spec['fragments']}
    return spec,kdir/'modules'/'lib/modules'/spec['release']

def regenerate_gate(cfg,cmd):
    # Replace the SMBIOS admission block of the default entry with the shared profile list.
    m=re.search(r"(menuentry '[^']*' \{\n)( insmod smbios\n.*?)( if \[ \"\$allowed\" = \"1\" \]; then\n)",cfg,re.S)
    if not m:raise ValueError('native-grub.cfg: admission block not found')
    cfg=cfg[:m.start(2)]+smbios_gate(cmd)+cfg[m.end(2):]
    return cfg.replace("This experiment is restricted to the tested HP 15-fe1xxx / 8C4F or QEMU.",
                       "This experiment requires a known hardware profile (exact SMBIOS match) or QEMU.")

def main():
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--assets',required=True,type=pathlib.Path,help='existing kit assets directory (read only)')
    p.add_argument('--output',required=True,type=pathlib.Path,help='NEW output assets directory')
    p.add_argument('--profile',default='hp-dragonfly-g4',choices=sorted(PROFILES))
    p.add_argument('--gki-modules',type=pathlib.Path,help='output directory of tools/build_gki_modules.py')
    p.add_argument('--camera',type=pathlib.Path,help='camera stack directory (layout of /oem/camera)')
    p.add_argument('--kernel',type=pathlib.Path,help='output directory of tools/build_profile_kernel.py --compile (profiles with a kernel)')
    p.add_argument('--surface',type=pathlib.Path,help='surface/build-iptsd.sh output directory (layout of /oem/surface)')
    p.add_argument('--firmware-cache',type=pathlib.Path,default=pathlib.Path('profile-firmware-cache'))
    a=p.parse_args();src=a.assets.resolve();out=a.output.absolute();profile=PROFILES[a.profile]
    if out.exists() or out.is_symlink():p.error('Output must not exist')
    if not shutil.which('debugfs') or not shutil.which('e2fsck'):p.error('Missing e2fsprogs (debugfs, e2fsck)')
    manifest=json.loads((src/'native-assets.json').read_text())
    for n,h in manifest['files'].items():
        if sha(src/n)!=h:raise ValueError('Source asset hash mismatch: '+n)
    cache=a.firmware_cache.resolve();fetched=[(e,fetch(e,cache)) for e in profile['files']]
    tmp=out.with_name(out.name+'.partial')
    if tmp.exists():p.error('Remove stale '+str(tmp))
    if profile.get('kernel') and not a.kernel:p.error('profile %s needs --kernel (tools/build_profile_kernel.py output)'%a.profile)
    if a.kernel and not profile.get('kernel'):p.error('profile %s does not use a replacement kernel'%a.profile)
    shutil.copytree(src,tmp,symlinks=False)
    try:
        kspec,kmods=(kernel_assets(a.kernel.resolve(),tmp,manifest,a.profile) if a.kernel else (None,None))
        if profile.get('stock_modules') is False:
            # Google's vendor_dlkm / system_dlkm modules cannot load on the replacement kernel.
            strip_oem_lines(tmp/'compat.ext4','/init.gbrunch.rc',r'\s*insmod /(vendor|system)/lib/modules/\S+')
        known={v['name'] for v in manifest['firmware']}
        for e,f in fetched:
            if e['target']=='initrd':
                if e['name'] in known:continue
                dest=tmp/'firmware'/e['name'];dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(f,dest)
                manifest['firmware'].append({'name':e['name'],'sha256':e['sha256'],'bytes':e['bytes'],'package':'linux-firmware '+COMMIT})
            elif e['target']=='license':
                shutil.copyfile(f,tmp/'licenses'/e['name'])
        add_oem_firmware(tmp/'compat.ext4',[(e,f) for e,f in fetched if e['target']=='oem'])
        for rel,label in profile.get('oem_files',[]):
            add_oem_file(tmp/'compat.ext4',PROFILE_DIR/a.profile/'oem'/rel,'/'+rel,label)
        if profile.get('vendor_files') or kspec or profile.get('policy_rules'):
            patch_tool_initrd(tmp/'tool-initrd.gz',profile.get('vendor_files',[]),PROFILE_DIR/a.profile,a.camera,no_stock_modules=bool(kspec),
                              policy_rules=profile.get('policy_rules',[]))
        mods=list(profile.get('oem_modules',[]))
        if any('module' in m for m in mods) and not kspec:raise ValueError("'module' entries need a replacement kernel (--kernel)")
        if any('file' in m for m in mods) and not a.gki_modules:raise ValueError('profile needs --gki-modules (tools/build_gki_modules.py output)')
        # Expand kernel-build module names to their dependency closure (modules.dep order); a module already
        # loaded at an earlier stage is not listed again.
        expanded=[];loaded=set()
        for m in mods:
            if 'module' in m:
                for c in module_closure(kmods.parent.parent.parent,kspec['release'],[m['module']]):
                    if c['file'] in loaded:continue
                    loaded.add(c['file']);e={'file':c['file'],'src':kmods/c['path'],'sha256':c['sha256'],'stage':m['stage']}
                    # 'args': module parameters for the insmod line of the named module itself (not its dependencies)
                    if m.get('args') and c['file'][:-3].replace('-','_')==m['module'].replace('-','_'):e['args']=m['args']
                    expanded.append(e)
            else:expanded.append(m)
        mods=expanded
        for m in mods:
            if 'stock' in m:continue
            ko=m.get('src') or a.gki_modules/m['file']
            if sha(ko)!=m['sha256']:raise ValueError('module hash mismatch: %s'%ko)
            add_oem_file(tmp/'compat.ext4',ko,'/modules/'+m['file'],'vendor_file')
        cams=profile.get('oem_camera',[])
        if any('src' not in c for c in cams) and not a.camera:raise ValueError('profile needs --camera (camera stack build output)')
        for c in cams:
            f=PROFILE_DIR/a.profile/c['src'] if 'src' in c else a.camera/c['file']
            if sha(f)!=c['sha256']:raise ValueError('camera file hash mismatch: %s'%f)
            add_oem_file(tmp/'compat.ext4',f,'/camera/'+c['file'],c.get('label','vendor_file'),c.get('mode','0644'))
        # Files the kit may not carry (vendor firmware): the manifest names them, the assembler takes them from
        # --local-files DIR, and the tool VM writes them into the locally built GBVENDOR. Empty placeholders in /oem
        # give init a bind-mount target inside firmware_class.path.
        local=[e for e in profile.get('vendor_files',[]) if 'local' in e]
        if local:manifest['local_files']=[{k:e[k] for k in ['local','sha256','bytes','fetch']} for e in local]
        else:manifest.pop('local_files',None)
        for rel in profile.get('oem_placeholders',[]):
            with tempfile.NamedTemporaryFile() as empty:add_oem_file(tmp/'compat.ext4',empty.name,'/'+rel,'vendor_firmware_file')
        surf=profile.get('oem_surface',[])
        if surf and not a.surface:raise ValueError('profile needs --surface (surface/build-iptsd.sh output)')
        for c in surf:
            f=a.surface/c['file']
            if sha(f)!=c['sha256']:raise ValueError('surface file hash mismatch: %s'%f)
            add_oem_file(tmp/'compat.ext4',f,'/surface/'+c['file'],c.get('label','vendor_file'),c.get('mode','0644'))
        if profile.get('init_append'):
            rc=tmp/'init-append.rc'
            subprocess.run(['debugfs','-R','dump /init.gbrunch.rc %s'%rc,str(tmp/'compat.ext4')],check=True,capture_output=True,env=ENV)
            text=rc.read_text();rc.unlink()
            replace_oem_text(tmp/'compat.ext4','/init.gbrunch.rc',text[-200:],text[-200:]+profile['init_append'])
        def insmods(stage):
            return ''.join('    insmod %s%s\n'%(m.get('stock') or '/oem/modules/'+m['file'],' '+m['args'] if m.get('args') else '')
                           for m in mods if m['stage']==stage)
        if profile.get('init_binds') or mods or profile.get('init_post_fs_data') or kspec:
            binds=''.join('    mount none %s %s bind\n'%(s,d) for s,d in profile.get('init_binds',[]))
            if kspec:
                # Replacement kernels are built without Rust, so there is no /dev/ashmem; the kernel answers the
                # ashmem ioctls on memfd instead (gki-kernel/patches/0001). libcutils only takes that path when
                # sys.use_memfd is true (it defaults to false and then opens /dev/ashmem: "Android Setup keeps
                # stopping" on the Surface Pro 7, first hardware boot 2026-11-09).
                binds+='    setprop sys.use_memfd true\n'
            binds+=insmods('early-init')+''.join('    %s\n'%c for c in profile.get('init_early',[]))
            replace_oem_text(tmp/'compat.ext4','/init.gbrunch.rc',INIT_ANCHOR,INIT_ANCHOR+binds)
            late=insmods('post-fs-data')+''.join('    %s\n'%c for c in profile.get('init_post_fs_data',[]))
            if late:replace_oem_text(tmp/'compat.ext4','/init.gbrunch.rc','on post-fs-data\n','on post-fs-data\n'+late)
        if profile.get('logger_capture'):
            replace_oem_text(tmp/'compat.ext4','/gbrunch-logger.sh',LOGGER_ANCHOR,LOGGER_CAPTURE+LOGGER_ANCHOR)
        if profile.get('logger_extra'):  # profile-specific captures, same shape as LOGGER_CAPTURE
            replace_oem_text(tmp/'compat.ext4','/gbrunch-logger.sh',LOGGER_ANCHOR,profile['logger_extra']+LOGGER_ANCHOR)
        compat=sha(tmp/'compat.ext4')
        if profile.get('compat_sha256') is None:
            print('WARNING: profile compat.ext4 is not pinned yet; derived image is '+compat,flush=True)
        elif manifest['files']['compat.ext4']==BASE_COMPAT and compat!=profile['compat_sha256']:
            raise ValueError('Derived compat.ext4 %s differs from the pinned profile image %s'%(compat,profile['compat_sha256']))
        if manifest['files']['compat.ext4']!=BASE_COMPAT:
            print('WARNING: source kit is not the tested V7 image; the trial/installer will refuse this compat.ext4',flush=True)
        extra=profile.get('cmdline_append','')
        if extra and extra not in manifest['cmdline']:
            if not re.fullmatch(r'[A-Za-z0-9_.=, -]+',extra):raise ValueError('Unsafe cmdline_append')
            manifest['cmdline']=manifest['cmdline'].replace(' androidboot.init_rc=',' '+extra+' androidboot.init_rc=',1)
            assert extra in manifest['cmdline'],'cmdline anchor androidboot.init_rc= not found'
        # The boot menu admits the board only if this build's command line carries its required options.
        if profile['board'] not in {v['board'] for v in googlebook_boot_menu.admitted(manifest['cmdline'])}:
            raise ValueError('cmdline_append lacks the boot menu requirements for board '+profile['board'])
        cfg=tmp/'native-grub.cfg';cfg.write_text(regenerate_gate(cfg.read_text(),manifest['cmdline']))
        # assemble_native_android.py renders grub.cfg with the googlebook_boot_menu.py beside it; ship this
        # branch's copy so the kit's own assemble admits the profile board.
        shutil.copyfile(pathlib.Path(googlebook_boot_menu.__file__),tmp/'googlebook_boot_menu.py')
        # The assembler beside the kit is this branch's copy too (it reads 'replacement_kernel' and kit modules).
        shutil.copyfile(pathlib.Path(__file__).with_name('assemble_native_android.py'),tmp/'assemble_native_android.py')
        # Kit facts (image name, firmware keys, notes) for tools/usb/make-usb-kit.sh and make-dev-kit.sh.
        kit=PROFILE_DIR/a.profile/'kit.json'
        if kit.is_file():
            if json.loads(kit.read_text()).get('profile')!=a.profile:raise ValueError('kit.json profile mismatch')
            shutil.copyfile(kit,tmp/'kit.json')
        record={'profile':a.profile,'board':profile['board'],'cmdline_append':profile.get('cmdline_append',''),'status':'profile: inventory-matched, Android boot not yet observed',
                'policy_rules':[list(r) for r in profile.get('policy_rules',[])],
                'evidence':profile['evidence'],'source':SOURCE,'source_kit_compat_sha256':manifest['files']['compat.ext4'],
                'files':[{k:e[k] for k in ['name','target','sha256','bytes']} for e,_ in fetched],
                'oem_modules':[{'file':m['file'],'sha256':m['sha256'],'stage':m['stage']} for m in mods if 'stock' not in m]}
        if kspec:record['kernel']={'release':kspec['release'],'sha256':kspec['kernel_sha256'],'source_commit':kspec['source_commit'],'patches':kspec['patches']}
        (tmp/('profile-'+a.profile+'.json')).write_text(json.dumps(record,indent=2)+'\n')
        files={}
        for f in sorted(tmp.rglob('*')):
            if f.is_file() and f.name!='native-assets.json':files[str(f.relative_to(tmp))]=sha(f)
        manifest['files']=files;manifest['scope']+=' + '+a.profile+' firmware profile'
        (tmp/'native-assets.json').write_text(json.dumps(manifest,indent=2)+'\n')
        tmp.rename(out)
    except BaseException:
        shutil.rmtree(tmp,ignore_errors=True);raise
    print(json.dumps({'output':str(out),'profile':a.profile,'initrd_firmware':len(manifest['firmware']),
                      'oem_firmware_added':sum(e['target']=='oem' for e,_ in fetched),'compat_sha256':files['compat.ext4']},indent=2))

if __name__=='__main__':main()
