# pages/notes.py — main notes/velocity view
PAGE_ID = 1
PAGE_NAME = "Notes"

from midicrt import draw_line, INSTRUMENT_NAMES
from plugins import polydisplay

def draw(state):
    y0 = state.get("y_offset", 3)
    for ch, name in enumerate(INSTRUMENT_NAMES, start=1):
        y = y0 + (ch - 1)
        notes = polydisplay.get_notes(ch)
        line = f"{ch:02d}  {name:<11}  {notes}"
        draw_line(y, line)
