#!/usr/bin/env bash
# smk25-autoconnect — keep the bonded SMK-25 BLE keyboard connected.
# BlueZ doesn't always re-initiate to a sleeping/woken BLE peripheral, so
# this loop nudges a connect whenever the bonded device is seen disconnected.
# Runs as a systemd service (smk25-autoconnect.service).
MAC="${SMK25_MAC:-D7:A7:62:D5:9B:BD}"
while true; do
    if bluetoothctl info "$MAC" 2>/dev/null | grep -q "Connected: no"; then
        bluetoothctl -- connect "$MAC" >/dev/null 2>&1
    fi
    sleep 15
done
