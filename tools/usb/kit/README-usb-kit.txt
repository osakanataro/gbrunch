GBRUNCH Googlebook USB kit for the @DEVICE@ (built locally by make-usb-kit.sh)

  @IMAGE@            disk image: Google's recovery + GBRUNCH. Do not share it.
  @IMAGE@.sha256     its hash
  @VENDOR_EROFS@     the GBVENDOR partition of that image (for apply-vendor-update.sh)
  usb/               grow-userdata.sh, reset-firstboot.sh, apply-profile-update.sh,
                     apply-vendor-update.sh, apply-image-update.sh

Profile notes:
@NOTES@
Status:
@STATUS@

Keep this folder on a data USB stick formatted as exFAT or ext4 (FAT32 cannot hold the image).
Secure Boot must be off: @SECURE_BOOT@
Every command below erases the whole target disk. Check the device name with
  lsblk -d -o NAME,SIZE,MODEL,TRAN

A. Boot from a USB disk (32 GB or larger; USB SSD recommended)
   On any Linux PC, with the target USB disk at /dev/sdX:
     sha256sum -c @IMAGE@.sha256
     sudo dd if=@IMAGE@ of=/dev/sdX bs=4M status=progress conv=fsync
     sudo partprobe /dev/sdX
     sudo bash usb/grow-userdata.sh /dev/sdX
   Boot the target from it. @BOOT_MENU@
   Firmware setup: @FIRMWARE_SETUP@

B. Install to the internal NVMe SSD (Windows and all data on it are erased)
   Boot the target from an Ubuntu live USB ("Try Ubuntu"), insert the data USB stick, open a terminal:
     cd /media/ubuntu/<stick>/<this folder>
     sha256sum -c @IMAGE@.sha256
     sudo umount /dev/nvme0n1p* 2>/dev/null
     sudo dd if=@IMAGE@ of=/dev/nvme0n1 bs=4M status=progress conv=fsync oflag=direct
     sudo partprobe /dev/nvme0n1
     sudo bash usb/grow-userdata.sh --internal /dev/nvme0n1
   (If sgdisk is missing: sudo apt install gdisk.) Power off, remove the USB sticks and boot.

C. Updating a disk that already runs Googlebook (userdata and logs are kept)
     sudo bash usb/apply-profile-update.sh [--internal] /dev/sdX assets-@PROFILE@   GBRUNCH (/oem) and kernel options
     sudo bash usb/apply-vendor-update.sh  [--internal] /dev/sdX @VENDOR_EROFS@      GBVENDOR
     sudo bash usb/apply-image-update.sh   [--internal] /dev/sdX @IMAGE@             EFI (kernel, initrd, GRUB), GBRUNCH, GBVENDOR
   @KERNEL_UPDATE@
   A kit built from a newer Google recovery: add --system to apply-image-update.sh. It also writes Google's
   system partitions (super, boot, vbmeta, ...) where they differ, and keeps userdata, metadata and GBLOG, so
   apps and settings stay, like an OTA update. Never use it to go back to an older recovery build.

grow-userdata.sh moves Android userdata to the free space of the disk; without it the first boot
runs out of space and restarts repeatedly. reset-firstboot.sh returns a disk to its first-boot state
(userdata erased, logs on GBLOG kept). Android logs are written to the GBLOG partition
(FAT, label GBRUNCH_LOG, Android-Logs/run-*/): read it from Linux after a test.
System updates (OTA) do not apply to this installation; a new Google recovery comes as a new kit (see C).
