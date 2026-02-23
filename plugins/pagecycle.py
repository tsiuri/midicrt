# -*- coding: utf-8 -*-
# Plugin: Page Cycler — automatically rotates through a list of pages

import time
import midicrt
from configutil import load_section, save_section

ENABLED = True
CYCLE_PAGES = [1, 6, 8, 9]   # page IDs to rotate through
INTERVAL = 5 * 60          # seconds per page (5 minutes)
USER_PAUSE = 60 * 60       # seconds to pause cycling after a keypress (60 minutes)

_cfg = load_section("pagecycle")
if _cfg is None:
    _cfg = {}
try:
    ENABLED = bool(_cfg.get("enabled", ENABLED))
    pages = _cfg.get("cycle_pages", CYCLE_PAGES)
    if isinstance(pages, str):
        try:
            CYCLE_PAGES = [int(p.strip()) for p in pages.split(",") if p.strip()]
        except Exception:
            pass
    elif isinstance(pages, list):
        CYCLE_PAGES = [int(p) for p in pages if isinstance(p, (int, float, str)) and str(p).strip()]
    INTERVAL = float(_cfg.get("interval", INTERVAL))
    USER_PAUSE = float(_cfg.get("user_pause", USER_PAUSE))
except Exception:
    pass

try:
    save_section("pagecycle", {
        "enabled": bool(ENABLED),
        "cycle_pages": list(CYCLE_PAGES),
        "interval": float(INTERVAL),
        "user_pause": float(USER_PAUSE),
    })
except Exception:
    pass

_last_switch = time.time()
_page_index = 0
_last_keypress = None


def notify_keypress():
    global _last_keypress
    _last_keypress = time.time()


def draw(state):
    global _last_switch, _page_index
    if not ENABLED:
        return
    if _last_keypress is not None and time.time() - _last_keypress < USER_PAUSE:
        return
    if time.time() - _last_switch >= INTERVAL:
        _page_index = (_page_index + 1) % len(CYCLE_PAGES)
        midicrt.current_page = CYCLE_PAGES[_page_index]
        _last_switch = time.time()
