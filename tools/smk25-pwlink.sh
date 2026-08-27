#!/usr/bin/env bash
# smk25-pwlink — keep the SMK-25's PipeWire BLE-MIDI node wired into the ALSA
# "Midi Through Port-0" bridge so knobctl (ALSA seq) reads it. PipeWire's
# BLE-MIDI parser preserves chord note-offs that bluez's parser drops.
# Runs as a systemd --user service in billie's PipeWire session.
NAME="${SMK25_PW:-bluez_midi.D7_A7_62_D5_9B_BD}"
while true; do
    src=$(pw-link -o -I 2>/dev/null | awk -v n="$NAME" 'index($0,n){print $1; exit}')
    dst=$(pw-link -i -I 2>/dev/null | awk 'tolower($0) ~ /midi through port-0/{print $1; exit}')
    if [ -n "$src" ] && [ -n "$dst" ]; then
        pw-link "$src" "$dst" 2>/dev/null   # idempotent: errors "already linked", ignored
    fi
    sleep 4
done
