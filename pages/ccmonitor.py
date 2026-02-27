# pages/ccmonitor.py — view recent MIDI CC messages
PAGE_ID = 4
PAGE_NAME = "CC Monitor"

from midicrt import draw_line
from collections import defaultdict, deque
from pages.legacy_contract_bridge import build_widget_from_legacy_contract

# Store last few CC messages per channel
_recent_ccs = defaultdict(lambda: deque(maxlen=6))

def handle(msg):
    """Capture incoming CC messages."""
    if msg.type == "control_change":
        _recent_ccs[msg.channel + 1].append((msg.control, msg.value))

def draw(state):
    """Draw the CC summary table."""
    y0 = int(state.get("y_offset", 3))
    cols = int(state.get("cols", 95))
    draw_line(y0, f"--- {PAGE_NAME} ---"[:cols])
    y = y0 + 1
    for ch in range(1, 17):
        events = list(_recent_ccs[ch])
        if events:
            # e.g., CC07:100 CC74:080 CC10:064
            cc_str = "  ".join(f"CC{num:02d}:{val:03d}" for num, val in events[-6:])
        else:
            cc_str = ""
        line = f"{ch:02d}  {cc_str}"
        draw_line(y, line[:cols])
        y += 1


def build_widget(state):
    return build_widget_from_legacy_contract(draw, state, draw_line)
