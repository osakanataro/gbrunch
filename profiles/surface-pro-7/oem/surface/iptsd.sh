#!/system/bin/sh
# GBRUNCH: run iptsd on the Surface's IPTS touchscreen (init service gbrunch.iptsd, domain shell, uid system,
# groups system/uhid/input: the owners of /dev/hidraw* and /dev/uinput, so no CAP_DAC_OVERRIDE is needed).
# The IPTS HID device appears once ipts.ko (loaded at post-fs-data) has set up the MEI client, so wait for it.
# iptsd creates uinput touchscreen and stylus devices, which Android's input stack picks up like any other.
# Logs go to logcat: logcat -s gbrunch-iptsd (the GBLOG touch snapshot collects them).
log() { /system/bin/log -p i -t gbrunch-iptsd "$*"; }
i=0
while [ "$i" -lt 60 ]; do
	for d in /dev/hidraw*; do
		[ -e "$d" ] || continue
		if /oem/surface/bin/iptsd-check-device -q -t touchscreen "$d" 2>/dev/null; then
			log "starting iptsd on $d"
			exec /oem/surface/bin/iptsd "$d"
		fi
	done
	i=$((i + 1))
	sleep 2
done
log "no IPTS touchscreen found after 120 s (is ipts.ko loaded? ls -lZ /dev/hidraw*)"
exit 0
