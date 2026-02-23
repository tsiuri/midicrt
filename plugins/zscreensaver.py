# -*- coding: utf-8 -*-
# Plugin: Screensaver — blank screen after MIDI idle to prevent burn-in
#
# Activates after IDLE_TIMEOUT seconds with no note_on, note_off, or
# control_change messages. Any keypress wakes it (handled in midicrt.py
# keyboard_listener).

import time
import sys
import midicrt
from configutil import load_section, save_section

IDLE_TIMEOUT = 60.0  # seconds of MIDI silence before activating

_cfg = load_section("screensaver")
if _cfg is None:
    _cfg = {}
try:
    IDLE_TIMEOUT = float(_cfg.get("idle_timeout", IDLE_TIMEOUT))
except Exception:
    pass

try:
    save_section("screensaver", {
        "idle_timeout": float(IDLE_TIMEOUT),
    })
except Exception:
    pass

_last_activity = time.time()
_active = False


def handle(msg):
    global _last_activity
    if msg.type in ("note_on", "note_off", "control_change"):
        _last_activity = time.time()


def is_active():
    return _active


def deactivate():
    global _active, _last_activity
    _active = False
    _last_activity = time.time()
    midicrt.last_header = ""


def draw(state):
    global _active
    if not _active and (time.time() - _last_activity) >= IDLE_TIMEOUT:
        _active = True

    if _active:
        t = midicrt.term
        sys.stdout.write(t.home + t.clear)
        sys.stdout.flush()
