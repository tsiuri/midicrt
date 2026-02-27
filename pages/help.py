# pages/help.py — key reference
PAGE_ID = 0
PAGE_NAME = "Help / Keys"

from midicrt import draw_line
from pages.legacy_contract_bridge import build_widget_from_legacy_contract

def draw(state):
    lines = [
        "[0] Help / Keybindings",
        "1 - Notes view",
        "2 - MIDI Ports",
        "3 - Transport info",
        "8 - Piano Roll",
        "9 - Audio Spectrum",
        "t - Tuner",
        "! - Chord+Key",
        "@ - Stuck Heatmap",
        "# - Voice Monitor",
        "$ - Config",
        "% - TimeSig Exp",
        "(Page 8) y - Piano roll style toggle",
        "0 - This help screen",
        "Q - Quit program",
    ]
    for i, l in enumerate(lines):
        draw_line(2 + i, l)


def build_widget(state):
    return build_widget_from_legacy_contract(draw, state, draw_line)
