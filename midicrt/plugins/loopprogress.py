# -*- coding: utf-8 -*-
# Plugin: Loop Progress Bar (works with plugin_state argument)
#
# Shows a moving '*' across [        ] resetting every 8 bars.

import sys

TOTAL_BARS = 8
BAR_WIDTH = 8
Y_POS_OFFSET = 2  # draw two lines from bottom

def draw(state):
    t = __import__("midicrt").term
    screen_rows = __import__("midicrt").SCREEN_ROWS
    y = screen_rows - Y_POS_OFFSET

    tick = state["tick"]
    bar = state["bar"]
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

    x = (__import__("midicrt").SCREEN_COLS // 2) - (len(visual) // 2)
    sys.stdout.write(t.move_yx(y, x) + visual)
    sys.stdout.flush()
