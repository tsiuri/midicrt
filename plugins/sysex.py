# -*- coding: utf-8 -*-
# Plugin: SysEx command receiver
#
# Listens for SysEx messages addressed to midicrt and dispatches commands.
# This allows remote control from the Cirklon or any device that can send SysEx.
#
# Message format:
#   F0  7D  6D  63  <cmd>  [args...]  F7
#
#   7D        = MIDI non-commercial / private-use manufacturer ID
#   6D 63     = ASCII 'mc' (midicrt identifier)
#   <cmd>     = command byte (see below)
#   [args...] = zero or more argument bytes (0–127)
#
# Commands:
#   01 <page>    — switch to any loaded page ID (current map: 0–15)
#   02 <0|1>     — screensaver: 0 = wake/deactivate, 1 = activate now
#   03 <0|1>     — page cycler: 0 = disable, 1 = enable

import sys
import time
import os
import midicrt

PREFIX = (0x7D, 0x6D, 0x63)  # non-commercial ID + 'mc'

MSG_DISPLAY_SECS = 5.0  # how long to show each command in the footer
SYSEX_LOG_PATH = os.path.join(os.path.dirname(midicrt.__file__), "sysex.log")
SYSEX_LOG_ALL = True  # log any incoming sysex (not just midicrt prefix)
SYSEX_SPLIT_DIR = os.path.join(os.path.dirname(midicrt.__file__), "sysex.d")
SYSEX_SPLIT_ENABLED = True
_sysex_seq = 0


def _log(text):
    midicrt.sysex_status = text
    midicrt.sysex_status_time = time.time()

def _log_sysex(data, note="rx"):
    if not SYSEX_LOG_PATH:
        return
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        hex_bytes = " ".join(f"{b:02X}" for b in data)
        full = f"F0 {hex_bytes} F7" if hex_bytes else "F0 F7"
        with open(SYSEX_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[SysEx] {ts} {note} {full}\n")
    except Exception:
        pass

def _split_sysex(data, note="rx"):
    global _sysex_seq
    if not SYSEX_SPLIT_ENABLED or not SYSEX_SPLIT_DIR:
        return
    try:
        os.makedirs(SYSEX_SPLIT_DIR, exist_ok=True)
        _sysex_seq += 1
        ts = time.strftime("%Y%m%d-%H%M%S")
        hex_bytes = " ".join(f"{b:02X}" for b in data)
        full = f"F0 {hex_bytes} F7" if hex_bytes else "F0 F7"
        fname = f"{ts}-{_sysex_seq:06d}-{note}.syx"
        path = os.path.join(SYSEX_SPLIT_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(full + "\n")
    except Exception:
        pass

def _find(attr):
    return next((m for m in midicrt.PLUGINS if hasattr(m, attr)), None)


def handle(msg):
    if msg.type != "sysex":
        return

    data = tuple(msg.data)
    if SYSEX_LOG_ALL:
        _log_sysex(data, "rx")
        _split_sysex(data, "rx")
    n = len(PREFIX)

    if len(data) < n + 1 or data[:n] != PREFIX:
        return

    # wake screensaver on any valid midicrt SysEx command
    ss = _find("is_active")
    if ss and ss.is_active():
        ss.deactivate()

    cmd  = data[n]
    args = data[n + 1:]
    _dispatch(cmd, args)


def _dispatch(cmd, args):
    # --- 01: switch page ---
    if cmd == 0x01:
        if not args:
            _log("sx:01 missing page")
            return
        page = args[0]
        ok, resolved = midicrt.switch_page(page)
        if not ok:
            _log(f"sx:01 invalid page {resolved}")
            return
        _log(f"sx:01 page→{resolved}")

    # --- 02: screensaver control ---
    elif cmd == 0x02:
        if not args:
            _log("sx:02 missing arg")
            return
        ss = _find("is_active")
        if not ss:
            _log("sx:02 no screensaver")
            return
        if args[0] == 0:
            ss.deactivate()
            _log("sx:02 screen on")
        else:
            ss._last_activity = time.time() - ss.IDLE_TIMEOUT - 1
            _log("sx:02 screen off")

    # --- 03: page cycler control ---
    elif cmd == 0x03:
        if not args:
            _log("sx:03 missing arg")
            return
        pc = _find("notify_keypress")
        if not pc:
            _log("sx:03 no cycler")
            return
        pc.ENABLED = bool(args[0])
        _log(f"sx:03 cycle {'on' if args[0] else 'off'}")

    else:
        _log(f"sx:?? cmd=0x{cmd:02X}")
