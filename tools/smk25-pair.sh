#!/usr/bin/env bash
# smk25-pair.sh — pair/connect the M-VAVE SMK-25 mini BLE MIDI keyboard to this
# Pi and wire its MIDI into ALSA-land where midicrt's knobctl plugin reads it.
#
# Usage:
#   smk25-pair            full flow: scan -> pair -> trust -> connect -> link
#   smk25-pair --link     just redo the PipeWire link (after a reconnect)
#   smk25-pair --status   show BT + PipeWire + ALSA state
#
# How the plumbing works (Debian 13 / PipeWire 1.4 / WirePlumber 0.5):
#   BlueZ itself does NOT expose BLE-MIDI as an ALSA seq client on Debian.
#   WirePlumber's bluez-midi monitor (enabled by default) creates a PipeWire
#   MIDI node for a connected BLE-MIDI device.  ALSA apps can't see PipeWire
#   nodes, so we pw-link the device node into the ALSA "Midi Through Port-0"
#   bridge node; the traffic then appears on ALSA seq client "Midi Through"
#   port 0, which plugins/knobctl.py includes in its input hints.
#   (Same trick as the mothership SMK-25 setup: route via Midi Through,
#   never virmidi.)  Port-1 of Midi Through belongs to netmidi — don't use it.
#
# Put the keyboard in Bluetooth pairing mode before running (power it on with
# BT mode active; see the SMK-25 manual — hold the BT/mode control until the
# LED blinks fast).

set -u
NAME_PAT="${SMK25_NAME_PAT:-SMK|M-VAVE|MVAVE}"
SCAN_SECS="${SMK25_SCAN_SECS:-20}"

bt() { bluetoothctl -- "$@" 2>&1; }

find_dev() {
    bluetoothctl devices | grep -iE "$NAME_PAT" | head -1
}

status() {
    echo "== bluetooth =="
    bluetoothctl show | grep -E "Powered|Discovering"
    bluetoothctl devices | grep -iE "$NAME_PAT" || echo "(no SMK-25 known)"
    d=$(find_dev)
    if [ -n "${d:-}" ]; then
        mac=$(echo "$d" | awk '{print $2}')
        bluetoothctl info "$mac" | grep -E "Connected|Paired|Trusted"
    fi
    echo "== pipewire midi nodes =="
    pw-link -o 2>/dev/null | grep -iE "$NAME_PAT|midi" | grep -vi through || echo "(no BLE midi output node)"
    echo "== links into Midi Through =="
    pw-link -l 2>/dev/null | grep -i -A1 "through" | head -10
    echo "== alsa seq =="
    aconnect -l | grep -A2 "Midi Through"
}

do_link() {
    # BLE device's midi OUTPUT port in the pw graph.  pw-link decorates its
    # listings with " (capture)"/" (playback)" suffixes that are not part of
    # the port name — strip them or the link call can't resolve the port.
    src=$(pw-link -o 2>/dev/null | grep -iE "$NAME_PAT" | head -1 | sed 's/ (capture)$//;s/ (playback)$//')
    # ALSA bridge node for Midi Through Port-0, playback side
    dst=$(pw-link -i 2>/dev/null | grep -i "through" | grep -i "port-0" | head -1 | sed 's/ (capture)$//;s/ (playback)$//')
    if [ -z "$src" ]; then
        echo "NO PipeWire output node matching /$NAME_PAT/ — is the keyboard connected?"
        echo "All pw midi outputs:"; pw-link -o | sed 's/^/  /'
        return 1
    fi
    if [ -z "$dst" ]; then
        echo "NO 'Midi Through Port-0' playback node in PipeWire graph:"
        pw-link -i | sed 's/^/  /'
        return 1
    fi
    echo "linking '$src' -> '$dst'"
    pw-link "$src" "$dst" 2>&1 | grep -v "already linked" || true
    echo "verify with: aseqdump -p 'Midi Through' (turn the knob / hit a key)"
}

case "${1:-}" in
  --status) status; exit 0 ;;
  --link)   do_link; exit $? ;;
esac

echo "[1/4] powering on + scanning ${SCAN_SECS}s for /$NAME_PAT/ (keyboard in pairing mode?)"
bt power on >/dev/null
bluetoothctl --timeout "$SCAN_SECS" scan on >/dev/null 2>&1 &
scanpid=$!
found=""
for i in $(seq "$SCAN_SECS"); do
    sleep 1
    found=$(find_dev)
    [ -n "$found" ] && break
done
kill "$scanpid" 2>/dev/null
if [ -z "$found" ]; then
    echo "not found. Is the SMK-25 advertising? (fast-blinking BT LED)"
    echo "Everything seen during scan:"; bluetoothctl devices | sed 's/^/  /'
    exit 1
fi
mac=$(echo "$found" | awk '{print $2}')
label=$(echo "$found" | cut -d' ' -f3-)
echo "found: $label ($mac)"

echo "[2/4] pairing"
bt pair "$mac" | tail -2
echo "[3/4] trusting (auto-reconnect) + connecting"
bt trust "$mac" | tail -1
bt connect "$mac" | tail -2
sleep 3

echo "[4/4] wiring MIDI into ALSA (Midi Through Port-0)"
do_link
echo
echo "Done. knobctl in midicrt scans for it automatically; on the LXP-1 page"
echo "press L then turn the knob to bind it. After a power-cycle of the"
echo "keyboard, rerun 'smk25-pair --link' if the knob goes quiet."
