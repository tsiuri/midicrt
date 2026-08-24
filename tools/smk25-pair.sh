#!/usr/bin/env bash
# smk25-pair.sh — pair/connect the M-VAVE SMK-25 mini BLE MIDI keyboard to this
# Pi so midicrt's knobctl plugin can read it.
#
# Usage:
#   smk25-pair            full flow: scan -> agent pair -> trust -> connect
#   smk25-pair --link     legacy PipeWire link step (only if no native seq port)
#   smk25-pair --status   show BT + ALSA + PipeWire state
#
# Plumbing notes (learned the hard way, 2026-08-24):
#   * Pairing MUST go through an interactive bluetoothctl session with a
#     NoInputNoOutput agent registered.  One-shot `bluetoothctl -- pair` has no
#     agent, so the SMK-25's passkey-confirm request dies with
#     "No agent available for request type 2" -> AuthenticationFailed, and the
#     half-trusted device then flaps connect/disconnect forever with
#     ServicesResolved stuck at false.
#   * Once properly bonded, THIS bluez build (Debian 13 +rpt) exposes BLE-MIDI
#     natively as an ALSA seq client named "SMK25Mini" — no PipeWire plumbing
#     needed; knobctl's "SMK" input hint matches it directly.  The pw-link →
#     "Midi Through Port-0" route is kept only as a fallback for stacks where
#     bluez lacks native MIDI (e.g. mothership; Port-1 belongs to netmidi).
#
# Put the keyboard in Bluetooth pairing mode before running.

set -u
NAME_PAT="${SMK25_NAME_PAT:-SMK|M-VAVE|MVAVE}"
SCAN_SECS="${SMK25_SCAN_SECS:-20}"

find_dev() {
    bluetoothctl devices | grep -iE "$NAME_PAT" | head -1
}

native_port() {
    aconnect -l | grep -iE "client [0-9]+: '.*($NAME_PAT)" | head -1
}

status() {
    echo "== bluetooth =="
    bluetoothctl show | grep -E "Powered|Discovering"
    d=$(find_dev)
    if [ -n "${d:-}" ]; then
        mac=$(echo "$d" | awk '{print $2}')
        bluetoothctl info "$mac" | grep -E "Name|Connected|Paired|Bonded|Trusted|Battery"
    else
        echo "(no SMK-25 known)"
    fi
    echo "== native ALSA seq port (preferred path) =="
    native_port || echo "(none — device disconnected or not bonded)"
    aconnect -l | grep -A2 -iE "($NAME_PAT)" | head -4
    echo "== pipewire (fallback path) =="
    pw-link -o 2>/dev/null | grep -iE "$NAME_PAT" || echo "(no pw node)"
}

do_link() {
    if [ -n "$(native_port)" ]; then
        echo "native ALSA seq port exists — no PipeWire link needed:"
        native_port
        return 0
    fi
    # pw-link decorates listings with " (capture)"/" (playback)" suffixes that
    # are not part of the port name — strip them or the link can't resolve.
    src=$(pw-link -o 2>/dev/null | grep -iE "$NAME_PAT" | head -1 | sed 's/ (capture)$//;s/ (playback)$//')
    dst=$(pw-link -i 2>/dev/null | grep -i "through" | grep -i "port-0" | head -1 | sed 's/ (capture)$//;s/ (playback)$//')
    if [ -z "$src" ]; then
        echo "NO PipeWire output node matching /$NAME_PAT/ — is the keyboard connected?"
        return 1
    fi
    if [ -z "$dst" ]; then
        echo "NO 'Midi Through Port-0' playback node in PipeWire graph"
        return 1
    fi
    echo "linking '$src' -> '$dst'"
    pw-link "$src" "$dst" 2>&1 | grep -v "already linked" || true
}

case "${1:-}" in
  --status) status; exit 0 ;;
  --link)   do_link; exit $? ;;
esac

bluetoothctl -- power on >/dev/null

# Pause the reconnect watcher so it can't re-grab the stale bond mid-flow.
sudo systemctl stop smk25-autoconnect.service 2>/dev/null
trap 'sudo systemctl start smk25-autoconnect.service 2>/dev/null' EXIT

echo "[1/3] scanning ${SCAN_SECS}s for /$NAME_PAT/ (keyboard in pairing mode?)"
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
    exit 1
fi
mac=$(echo "$found" | awk '{print $2}')
echo "found: $(echo "$found" | cut -d' ' -f3-) ($mac)"

# The full flow is run when things are broken, and a device freshly put in
# pairing mode has NEW keys — any kept Pi-side bond is stale by definition
# (symptom: "Connected: yes" but bluetoothd logs "MIDI I/O: notifications
# not enabled" and the keyboard LED keeps blinking). Always start clean.
if bluetoothctl devices | grep -qi "$mac"; then
    echo "     removing existing bond first (pairing-mode keys are always new)"
    bluetoothctl -- remove "$mac" >/dev/null 2>&1
    sleep 3
fi

echo "[2/3] pairing with NoInputNoOutput agent (auto-confirm)"
{
    echo "agent NoInputNoOutput"; sleep 1
    echo "default-agent";         sleep 1
    echo "scan on";               sleep 8
    echo "scan off"
    echo "pair $mac";             sleep 10
    echo "trust $mac";            sleep 2
    echo "connect $mac";          sleep 6
    echo "quit"
} | bluetoothctl 2>&1 | grep -E "Pairing|Paired|Bonded|Connection|Failed|AuthenticationFailed" | tail -6

sleep 3
if ! bluetoothctl info "$mac" | grep -q "Bonded: yes"; then
    echo "PAIRING DID NOT BOND — put the keyboard back in pairing mode and rerun."
    bluetoothctl info "$mac" | grep -E "Paired|Bonded|Connected"
    exit 1
fi

echo "[3/3] MIDI wiring"
sleep 2
if [ -n "$(native_port)" ]; then
    echo "bonded + native ALSA seq port present:"
    native_port
else
    echo "no native seq port; trying PipeWire fallback"
    do_link
fi
echo
echo "Done. knobctl in midicrt rescans every few seconds; on the LXP-1 page"
echo "press L then turn the knob to (re)bind it. If midicrt was started before"
echo "this pairing, restart it so knobctl grabs the SMK port instead of the"
echo "Midi Through fallback."
