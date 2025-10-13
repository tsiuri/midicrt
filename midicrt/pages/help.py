# pages/help.py — key reference
PAGE_ID = 0
PAGE_NAME = "Help / Keys"

from midicrt import draw_line

def draw(state):
    lines = [
        "[0] Help / Keybindings",
        "1 - Notes view",
        "2 - MIDI Ports",
        "3 - Transport info",
        "0 - This help screen",
        "Q - Quit program",
    ]
    for i, l in enumerate(lines):
        draw_line(2 + i, l)
