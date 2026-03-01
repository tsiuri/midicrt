# pages/help.py — key reference
PAGE_ID = 0
PAGE_NAME = "Help / Keys"

from midicrt import draw_line
from ui.model import PageLinesWidget

def draw(state):
    lines = _build_widget_lines(state)
    for i, l in enumerate(lines):
        draw_line(2 + i, l)


def _build_widget_lines(_state):
    return [
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
        "^ - Piano Roll Exp",
        "& - MIDI IMG2TXT",
        "(Page 16) h - toggle memory mode (paged capture)",
        "(Page 17) MIDI + spectrum reactive ascii image translation",
        "(Page 8) y - Piano roll style toggle",
        "0 - This help screen",
        "Q - Quit program",
    ]


def build_widget(state):
    return PageLinesWidget(page_id=PAGE_ID, page_name=PAGE_NAME, lines=_build_widget_lines(state))
