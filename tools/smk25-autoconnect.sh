#!/usr/bin/env bash
# smk25-autoconnect — keep the bonded SMK-25 BLE MIDI keyboard connected,
# fully hands-off: power the keyboard on and it attaches within ~10s.
#
# Runs as root (system service). Each cycle it (1) clears any rfkill
# soft-block on the controller, (2) ensures the adapter is powered, then
# (3) reconnects the bonded device if it dropped. bluez creates the native
# "SMK25Mini" ALSA seq port on connect; midicrt's knobctl grabs it on its
# own rescan. Nothing here needs the keyboard to be present — it just waits.
MAC="${SMK25_MAC:-D7:A7:62:D5:9B:BD}"

ensure_adapter() {
    # rfkill binary isn't installed on this Pi; clear the soft-block via sysfs
    for f in /sys/class/rfkill/*/; do
        [ "$(cat "$f/name" 2>/dev/null)" = "hci0" ] && echo 0 > "$f/soft" 2>/dev/null
    done
    if ! bluetoothctl show 2>/dev/null | grep -q "Powered: yes"; then
        hciconfig hci0 up 2>/dev/null
        btmgmt power on >/dev/null 2>&1
        bluetoothctl power on >/dev/null 2>&1
    fi
}

while true; do
    ensure_adapter
    if ! bluetoothctl info "$MAC" 2>/dev/null | grep -q "Connected: yes"; then
        bluetoothctl -- connect "$MAC" >/dev/null 2>&1
    fi
    sleep 10
done
