# -*- coding: utf-8 -*-
# Plugin: Beat counter for midicrt
import sys
import midicrt  # the main script is importable as a module

def draw():
    # Only draw when transport is running; use the shared tick counter for stability.
    if not midicrt.running:
        return
    ticks = midicrt.tick_counter % 96  # 24 PPQN * 4 beats
    beats = (ticks // 24) + 1
    bar = midicrt.bar_counter
    line = f"BAR {bar:04d}  BEAT {beats:02d}  TICK {ticks:03d}"

    # Draw in a non-conflicting area near the bottom to avoid fighting with row 1.
    y = max(0, midicrt.SCREEN_ROWS - 4)
    sys.stdout.write(midicrt.term.move_yx(y, 0) + line.ljust(midicrt.SCREEN_COLS))
