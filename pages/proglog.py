# pages/proglog.py — program-change event log page
BACKGROUND = True
PAGE_ID = 7
PAGE_NAME = "Program Changes"

from collections import deque
import time
from blessed import Terminal
from midicrt import draw_line
from ui.model import PageLinesWidget

term = Terminal()

# rolling buffer of lines
MAX_LOG = 300
log_buffer = deque(maxlen=MAX_LOG)

# scrolling
scroll_offset = 0
VISIBLE_ROWS_TARGET = 200

# ---------- event handler ----------
def handle(msg):
    if msg.type != "program_change":
        return
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}]  Ch{msg.channel + 1:02d} → Program {msg.program:03d}"
    log_buffer.append(line)

# ---------- scrolling helpers ----------
def scroll_up():
    global scroll_offset
    if scroll_offset + 1 < len(log_buffer):
        scroll_offset += 1

def scroll_down():
    global scroll_offset
    if scroll_offset > 0:
        scroll_offset -= 1

def page_up():
    global scroll_offset
    scroll_offset = min(len(log_buffer) - 1, scroll_offset + VISIBLE_ROWS_TARGET)

def page_down():
    global scroll_offset
    scroll_offset = max(0, scroll_offset - VISIBLE_ROWS_TARGET)

def scroll_home():
    global scroll_offset
    scroll_offset = len(log_buffer)

def scroll_end():
    global scroll_offset
    scroll_offset = 0

# ---------- draw ----------
def draw(state):
    y0 = state.get("y_offset", 3)
    cols = state["cols"]
    rows = state["rows"]

    # header
    draw_line(y0, f"--- {PAGE_NAME} ---")

    # compute available space for body
    top = y0 + 2
    bottom = rows - 2
    visible_rows = max(5, min(VISIBLE_ROWS_TARGET, bottom - top))

    if len(log_buffer) == 0:
        draw_line(top, "(no program-change events yet)")
        return

    start_index = max(0, len(log_buffer) - visible_rows - scroll_offset)
    end_index = start_index + visible_rows
    visible = list(log_buffer)[start_index:end_index]

    y = top
    last_y = y
    for line in visible:
        draw_line(y, line[:cols])
        last_y = y
        y += 1
        if y >= top + visible_rows:
            break

    # scroll marker, 4-char safety margin
    total = len(log_buffer)
    pos = max(0, total - visible_rows - scroll_offset)
    percent = int((pos / max(1, total - visible_rows)) * 100)
    if scroll_offset == 0:
        marker = f"  ⟵ end of log ({percent:3d}%)"
    else:
        marker = f"  ⟵ offset {scroll_offset} ({percent:3d}%)"

    base = visible[-1] if visible else ""
    gap = 4
    usable_cols = cols - gap
    space = max(0, usable_cols - len(base) - len(marker))
    merged = (base[:usable_cols] + " " * space + marker)[:cols - 1]
    draw_line(last_y, merged)


def _build_widget_lines(state):
    cols = int(state.get("cols", 95))
    rows = int(state.get("rows", 30))
    lines = [f"--- {PAGE_NAME} ---", ""]
    top = 5
    bottom = rows - 2
    visible_rows = max(5, min(VISIBLE_ROWS_TARGET, bottom - top))
    if len(log_buffer) == 0:
        lines.append("(no program-change events yet)")
        return lines
    start_index = max(0, len(log_buffer) - visible_rows - scroll_offset)
    visible = list(log_buffer)[start_index:start_index + visible_rows]
    for line in visible:
        lines.append(line[:cols])
    total = len(log_buffer)
    pos = max(0, total - visible_rows - scroll_offset)
    percent = int((pos / max(1, total - visible_rows)) * 100)
    marker = f"  ⟵ end of log ({percent:3d}%)" if scroll_offset == 0 else f"  ⟵ offset {scroll_offset} ({percent:3d}%)"
    base = visible[-1] if visible else ""
    usable_cols = cols - 4
    space = max(0, usable_cols - len(base) - len(marker))
    merged = (base[:usable_cols] + " " * space + marker)[:cols - 1]
    lines[-1] = merged
    return lines


def build_widget(state):
    return PageLinesWidget(page_id=PAGE_ID, page_name=PAGE_NAME, lines=_build_widget_lines(state))
