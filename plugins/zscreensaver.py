# -*- coding: utf-8 -*-
# Plugin: Screensaver — blank framebuffer after MIDI idle to prevent burn-in
#
# Activates after IDLE_TIMEOUT seconds with no MIDI activity.
# Any keypress or MIDI event wakes it (via duck-typing in midicrt.py).
# Blanking writes zeros directly to /dev/fb0 (true black, RGB565).

import time
import mmap
import midicrt
from configutil import load_section, save_section

FB_PATH    = "/dev/fb0"
FB_WIDTH   = 800
FB_HEIGHT  = 475
FB_SIZE    = FB_WIDTH * FB_HEIGHT * 2  # RGB565 = 2 bytes/pixel

IDLE_TIMEOUT = 60.0

_cfg = load_section("screensaver")
if _cfg is None:
    _cfg = {}
try:
    IDLE_TIMEOUT = float(_cfg.get("idle_timeout", IDLE_TIMEOUT))
except Exception:
    pass

try:
    save_section("screensaver", {"idle_timeout": float(IDLE_TIMEOUT)})
except Exception:
    pass

_last_activity = time.time()
_active        = False
_fb_file       = None
_fb_mmap       = None


def _open_fb():
    global _fb_file, _fb_mmap
    if _fb_mmap is not None:
        return True
    try:
        _fb_file = open(FB_PATH, "r+b", buffering=0)
        _fb_mmap = mmap.mmap(_fb_file.fileno(), FB_SIZE)
        return True
    except Exception:
        return False


def _blank_fb():
    if not _open_fb():
        return
    try:
        _fb_mmap[:] = b'\x00' * FB_SIZE
        _fb_mmap.flush()
    except Exception:
        pass


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
        _blank_fb()
