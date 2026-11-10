#!/system/bin/sh
# GBRUNCH: dump the ALSA state of the SOF sound card to logcat (tag gbrunch-audiodump), once per boot.
# Service gbrunch.audiodump: domain shell, uid audio (owner of /dev/snd/*), oneshot. The GBLOG snapshots
# collect the output (NN-audio-controls.txt), so the DRAS mixer_paths.xml can be written from the device's
# real control names without a shell on the device.
log() { /system/bin/log -p i -t gbrunch-audiodump "$*"; }
i=0
while [ "$i" -lt 45 ]; do
	[ -e /dev/snd/controlC0 ] && break
	i=$((i + 1)); sleep 2
done
if [ ! -e /dev/snd/controlC0 ]; then
	log "no /dev/snd/controlC0 after 90 s (sound card did not probe)"; exit 0
fi
sleep 5
log "== /proc/asound/cards"; while read -r l; do log "$l"; done < /proc/asound/cards
log "== /proc/asound/pcm"; while read -r l; do log "$l"; done < /proc/asound/pcm
for c in 0 1; do
	[ -e "/dev/snd/controlC$c" ] || continue
	log "== tinymix -D $c controls"
	/oem/surface/bin/tinymix -D "$c" contents 2>&1 | while read -r l; do log "$l"; done
done
log "== done"
exit 0
