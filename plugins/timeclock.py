# -*- coding: utf-8 -*-
# Musical + Real-Time + Session Timer Clock
# Reverse blink written in a second pass (no width issues, no extra "BAR")

import sys, time

PPQN = 24
BEATS_PER_BAR = 4
Y_POS_OFFSET = 3

REV_ON  = "\x1b[7m"
REV_OFF = "\x1b[27m"

_state = globals().setdefault("_timeclock_state", {
    "start_time": None,
    "accumulated": 0.0,
    "last_running": False,
    "first_play_after_stop": True,
    "last_blink_beat": -1,
    "blink_state": False,
})

def draw(state):
    import midicrt
    t = midicrt.term
    y = midicrt.SCREEN_ROWS - Y_POS_OFFSET
    xmid = midicrt.SCREEN_COLS // 2

    tick = state["tick"]
    bar = state["bar"]
    running = state["running"]
    now = time.time()
    s = _state

    # --- transport handling ---
    if running and not s["last_running"]:
        if s["first_play_after_stop"]:
            s["accumulated"] = 0.0
            s["start_time"] = now
            s["first_play_after_stop"] = False
        elif s["start_time"] is None:
            s["start_time"] = now
    elif not running and s["last_running"]:
        if s["start_time"] is not None:
            s["accumulated"] += now - s["start_time"]
            s["start_time"] = None
        s["first_play_after_stop"] = True
    s["last_running"] = running

    # --- elapsed time ---
    elapsed = s["accumulated"] + ((now - s["start_time"]) if running and s["start_time"] else 0)
    hrs = int(elapsed // 3600)
    mins = int((elapsed % 3600) // 60)
    secs = int(elapsed % 60)
    ms = int((elapsed * 1000) % 1000)
    timer_str = f"{hrs:02d}:{mins:02d}:{secs:02d}.{ms:03d}"

    # --- beat-synced blink ---
    if running:
        beat_index = tick // PPQN
        if beat_index != s["last_blink_beat"]:
            s["blink_state"] = not s["blink_state"]
            s["last_blink_beat"] = beat_index
    else:
        s["blink_state"] = False

    # --- musical + wall clocks ---
    ticks_in_bar = tick % (PPQN * BEATS_PER_BAR)
    beat_in_bar = (tick // PPQN) % BEATS_PER_BAR + 1
    lt = time.localtime(now)
    ms_now = int((now % 1) * 1000)
    realtime_str = time.strftime("%H:%M:%S", lt) + f".{ms_now:03d}"

    # --- full-line draw ---
    base_text = (
        f"BAR {bar:04d}  BEAT {beat_in_bar:02d}  TICK {ticks_in_bar:03d}   "
        f"{realtime_str}   TIMER {timer_str}"
    )
    x = xmid - (len(base_text) // 2)
    sys.stdout.write(t.move_yx(y, x) + base_text.ljust(midicrt.SCREEN_COLS))

    # --- overlay reverse label safely ---
    if s["blink_state"] and running:
        # find where "TIMER" starts relative to the base line
        label_pos = base_text.find("TIMER")
        if label_pos >= 0:
            sys.stdout.write(
                t.move_yx(y, x + label_pos) + REV_ON + "TIMER" + REV_OFF
            )

    sys.stdout.flush()
