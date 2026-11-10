GBRUNCH release kit: builds a Googlebook OS disk image from Google's official recovery

This kit holds open-source and redistributable files only (see NOTICE.txt). It does not contain
Google's software: make-usb-kit.sh downloads the official recovery (ruby 16452207, 7.8 GB) from
dl.google.com, checks its sha256, and builds the image on your PC.

Requirements: an x86_64 Linux PC (Ubuntu 24.04 or later), about 50 GB free, and
  sudo apt install python3 qemu-system-x86 qemu-utils mtools lz4
No root is needed for the build. KVM is not used.

  tar xf gbrunch-*-kit-*.tar.gz
  bash gbrunch-*-kit-*/make-usb-kit.sh ~/gbrunch-out
  # or, with a recovery.zip downloaded already:
  bash gbrunch-*-kit-*/make-usb-kit.sh --recovery-zip recovery.zip ~/gbrunch-out

The result is ~/gbrunch-out/usb-kit/ (image, hash, usb/ scripts and README.txt with the steps for a
USB disk or the internal NVMe SSD, with the firmware keys of the kit's laptop) and the GBVENDOR image for
apply-vendor-update.sh. The build log is ~/gbrunch-out/<image>.build-vm.log; the image name is in assets-*/kit.json.

Files: make-usb-kit.sh, README-usb-kit.txt (copied into usb-kit/), assets-*/ (build assets), usb/,
licenses/, NOTICE.txt, SHA256SUMS (checked by make-usb-kit.sh).
