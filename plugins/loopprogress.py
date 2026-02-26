# -*- coding: utf-8 -*-
# Plugin: Loop Progress Bar (works with plugin_state argument)
#
# Shows a moving '*' across [        ] resetting every 8 bars.
# Also renders sysex status to the left of the bar.

import sys
import time

TOTAL_BARS = 8
BAR_WIDTH = 8
Y_POS_OFFSET = 2  # draw two lines from bottom
SYSEX_DISPLAY_SECS = 5.0

def draw(state):
    import midicrt
    t = midicrt.term
    screen_rows = midicrt.SCREEN_ROWS
    screen_cols = midicrt.SCREEN_COLS
    y = screen_rows - Y_POS_OFFSET

    tick = state["tick"]
    running = state["running"]

    TICKS_PER_BAR = 24 * 4
    TOTAL_TICKS = TOTAL_BARS * TICKS_PER_BAR

    # progress through 8-bar cycle
    frac = (tick % TOTAL_TICKS) / TOTAL_TICKS
    pos = int(frac * BAR_WIDTH)

    bar_chars = [" "] * BAR_WIDTH
    if running:
        bar_chars[pos % BAR_WIDTH] = "*"
    visual = "[" + "".join(bar_chars) + "]"

    x_bar = (screen_cols // 2) - (len(visual) // 2)

    # Footer status shown to the left of the bar: FPS + recent SysEx summary.
    left_width = max(0, x_bar - 1)
    fps = (getattr(midicrt, "fps_status", "") or "").strip()
    sx = midicrt.sysex_status
    sx_text = ""
    if sx and (time.time() - midicrt.sysex_status_time) < SYSEX_DISPLAY_SECS:
        sx_text = sx.strip()
    parts = [p for p in (fps, sx_text) if p]
    status = "  ".join(parts)
    left_text = status[:left_width].ljust(left_width) if status else (" " * left_width)

    sys.stdout.write(t.move_yx(y, 0) + left_text + " " + visual)
    sys.stdout.flush()
