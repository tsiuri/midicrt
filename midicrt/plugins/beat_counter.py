# -*- coding: utf-8 -*-
# Plugin: Beat counter for midicrt
import time
import midicrt  # the main script is importable as a module

def draw():
    bps = midicrt.bpm / 60.0 if midicrt.bpm else 0
    t = time.time()
    if midicrt.running and bps > 0:
        ticks = int((t - (midicrt.last_clock_ts or t)) * bps * 96) % 96
        bar = midicrt.bar_counter
        beats = (ticks // 24) + 1
        line = f"BAR {bar:04d}  BEAT {beats:02d}  TICK {ticks:03d}"
        print(midicrt.term.move_yx(1, 0) + line.ljust(midicrt.SCREEN_COLS))
