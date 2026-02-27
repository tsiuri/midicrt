# pages/ccgraph.py — perfectly aligned CC dashboard
BACKGROUND = True
PAGE_ID = 5
PAGE_NAME = "CC Dashboard"

from midicrt import draw_line
from collections import OrderedDict
import time
from blessed import Terminal
from pages.legacy_contract_bridge import build_widget_from_legacy_contract
from ui.model import PageLinesWidget

term = Terminal()

_recent = OrderedDict()
MAX_ENTRIES = 16  # fits 30-row CRT

def handle(msg):
    if msg.type == "control_change":
        key = (msg.channel + 1, msg.control)
        if key not in _recent and len(_recent) >= MAX_ENTRIES:
            _recent.popitem(last=False)
        _recent[key] = (time.time(), msg.value)

def draw(state):
    lines = _build_widget_lines(state)
    y0 = 3
    max_cols = state["cols"]
    for idx, line in enumerate(lines):
        draw_line(y0 + idx, line[: max_cols - 1])


def _build_widget_lines(state):
    now = time.time()
    rows = int(state.get("rows", 30))
    cols = int(state.get("cols", 95))
    lines = [f"--- {PAGE_NAME} ---", ""]

    label_width = 18
    bar_region_width = max(8, cols - label_width - 34)
    max_rows = max(0, rows - 5)
    for (ch, cc), (ts, val) in list(_recent.items())[:max_rows]:
        age = now - ts
        bar_len = min(bar_region_width, int((val / 127) * bar_region_width))
        bar = "█" * bar_len
        label = f"Ch{ch:02d} CC{cc:03d}:{val:03d}"
        age_str = "LIVE" if age < 2 else f"{age:5.1f}s"
        lines.append(f"{label:<{label_width}}{bar:<{bar_region_width}}  {age_str} ")
    if not _recent:
        lines.append("(no CC activity)")
    return lines


def build_widget(state):
    return PageLinesWidget(page_id=PAGE_ID, page_name=PAGE_NAME, lines=_build_widget_lines(state))
