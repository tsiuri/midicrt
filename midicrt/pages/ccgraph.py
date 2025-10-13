# pages/ccgraph.py — perfectly aligned CC dashboard
BACKGROUND = True
PAGE_ID = 5
PAGE_NAME = "CC Dashboard"

from midicrt import draw_line
from collections import OrderedDict
import time
from blessed import Terminal

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
    y0 = 3
    draw_line(y0, f"--- {PAGE_NAME} ---")

    now = time.time()
    y = y0 + 2
    max_y = state["rows"] - 3
    max_cols = state["cols"]

    label_width = 18
    # Reserve 34 columns for right-side meter zone (1 more than before)
    bar_region_width = max_cols - label_width - 34
    if bar_region_width < 8:
        bar_region_width = 8

    for (ch, cc), (ts, val) in _recent.items():
        if y >= max_y:
            break

        age = now - ts
        bar_len = min(bar_region_width, int((val / 127) * bar_region_width))
        bar = "█" * bar_len
        label = f"Ch{ch:02d} CC{cc:03d}:{val:03d}"

        # persistent age indicator (no disappearance)
        if age < 2:
            age_str = term.reverse("LIVE") + term.normal
        else:
            age_str = f"{age:5.1f}s"

        # compose and safely clamp to screen width
        line = f"{label:<{label_width}}{bar:<{bar_region_width}}  {age_str} "
        draw_line(y, line[:max_cols - 1])
        y += 1

    if not _recent:
        draw_line(y, "(no CC activity)")
