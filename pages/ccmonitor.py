# pages/ccmonitor.py — view recent MIDI CC messages
PAGE_ID = 4
PAGE_NAME = "CC Monitor"

from midicrt import draw_line
from collections import defaultdict, deque
from ui.model import PageLinesWidget

# Store last few CC messages per channel
_recent_ccs = defaultdict(lambda: deque(maxlen=6))

def handle(msg):
    """Capture incoming CC messages."""
    if msg.type == "control_change":
        _recent_ccs[msg.channel + 1].append((msg.control, msg.value))

def draw(state):
    """Draw the CC summary table."""
    lines = _build_widget_lines(state)
    y0 = int(state.get("y_offset", 3))
    cols = int(state.get("cols", 95))
    for idx, line in enumerate(lines):
        draw_line(y0 + idx, line[:cols])


def _build_widget_lines(_state):
    lines = [f"--- {PAGE_NAME} ---"]
    for ch in range(1, 17):
        events = list(_recent_ccs[ch])
        cc_str = "  ".join(f"CC{num:02d}:{val:03d}" for num, val in events[-6:]) if events else ""
        lines.append(f"{ch:02d}  {cc_str}")
    return lines


def build_widget(state):
    return PageLinesWidget(page_id=PAGE_ID, page_name=PAGE_NAME, lines=_build_widget_lines(state))
