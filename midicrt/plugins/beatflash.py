# -*- coding: utf-8 -*-
# Plugin: Metronome Flash (bottom visible version)
#
# A visual beat indicator that flashes every quarter note (24 MIDI clocks).
# Draws a small reversed block on the bottom line so it's never overwritten.

import midicrt
import time
import sys

PPQN = 24  # MIDI clocks per quarter note
_last_tick = 0
_flash_state = False
_last_flash_time = 0

def draw():
    global _last_tick, _flash_state, _last_flash_time
    now = time.time()
    t = midicrt.term
    tick = midicrt.tick_counter
    running = midicrt.running

    if not running:
        return

    # detect new beat
    if tick // PPQN != _last_tick // PPQN:
        _flash_state = True
        _last_flash_time = now
        _last_tick = tick

    # turn off after 0.1 s
    if _flash_state and (now - _last_flash_time) > 0.1:
        _flash_state = False

    # choose appearance
    symbol = "██" if _flash_state else "  "
    visual = t.reverse(symbol) if _flash_state else symbol

    # draw on bottom line, left corner
    y = midicrt.SCREEN_ROWS - 1
    x = 0
    sys.stdout.write(t.move_yx(y, x) + visual + t.normal)
    sys.stdout.flush()
