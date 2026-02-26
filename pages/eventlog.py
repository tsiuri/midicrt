# pages/eventlog.py — general MIDI event log with CC filter input
BACKGROUND = True
PAGE_ID = 6
PAGE_NAME = "Event Log"

from collections import deque
import time
from blessed import Terminal
from midicrt import draw_line
from configutil import load_section, save_section
from ui.model import EventLogWidget
from ui.adapters import build_widget_from_legacy_draw

term = Terminal()

MAX_LOG = 300
VISIBLE_ROWS_TARGET = 200

_cfg = load_section("eventlog")
if _cfg is None:
    _cfg = {}
try:
    MAX_LOG = int(_cfg.get("max_log", MAX_LOG))
    VISIBLE_ROWS_TARGET = int(_cfg.get("visible_rows_target", VISIBLE_ROWS_TARGET))
except Exception:
    pass

try:
    save_section("eventlog", {
        "max_log": int(MAX_LOG),
        "visible_rows_target": int(VISIBLE_ROWS_TARGET),
    })
except Exception:
    pass

log_buffer = deque(maxlen=MAX_LOG)
scroll_offset = 0

# Track note-on times for duration logging
note_on_times = {}  # (ch, note) -> [timestamps]

# --------- filters ---------
filters = {
    "type": None,        # "control_change" etc.
    "channel": None,     # 1–16
    "control": None,     # CC number
}
filter_input_mode = False
filter_input_text = ""

def passes_filter(msg):
    if filters["type"] and msg.type != filters["type"]:
        return False
    if filters["channel"] and (msg.channel + 1) != filters["channel"]:
        return False
    if filters["control"] and getattr(msg, "control", None) != filters["control"]:
        return False
    return True

# ---------- MIDI handler ----------
def handle(msg):
    if not passes_filter(msg):
        return
    ts = time.strftime("%H:%M:%S")
    now = time.time()
    desc = msg.type
    if msg.type == "note_on":
        ch = msg.channel + 1
        if msg.velocity == 0:
            stack = note_on_times.get((ch, msg.note), [])
            start = stack.pop() if stack else None
            if not stack:
                note_on_times.pop((ch, msg.note), None)
            duration = (now - start) if start is not None else None
            desc += f"  Ch{ch:02d} Note {msg.note:03d} vel 000"
            if duration is not None:
                desc += f" [{duration:0.3f}s]"
        else:
            note_on_times.setdefault((ch, msg.note), []).append(now)
            desc += f"  Ch{ch:02d} Note {msg.note:03d} vel {msg.velocity:03d}"
    elif msg.type == "note_off":
        ch = msg.channel + 1
        stack = note_on_times.get((ch, msg.note), [])
        start = stack.pop() if stack else None
        if not stack:
            note_on_times.pop((ch, msg.note), None)
        duration = (now - start) if start is not None else None
        desc += f"  Ch{ch:02d} Note {msg.note:03d} vel {msg.velocity:03d}"
        if duration is not None:
            desc += f" [{duration:0.3f}s]"
    elif msg.type == "control_change":
        desc += f"  Ch{msg.channel+1:02d} CC{msg.control:03d}={msg.value:03d}"
    elif msg.type == "program_change":
        desc += f"  Ch{msg.channel+1:02d} Prog {msg.program:03d}"
    else:
        desc += f"  Ch{msg.channel+1:02d}"
    log_buffer.append(f"[{ts}] {desc}")

# ---------- keyboard control ----------
def keypress(ch):
    global filter_input_mode, filter_input_text, scroll_offset

    # ---------- FILTER MODE ----------
    if filter_input_mode:
        if ch.name == "KEY_ENTER":
            text = filter_input_text.strip()
            if not text:
                # Empty entry clears all filters
                filters["type"] = filters["channel"] = filters["control"] = None
            else:
                try:
                    cc_num = int(text)
                    filters["type"] = "control_change"
                    filters["control"] = cc_num
                except ValueError:
                    pass
            filter_input_mode = False
            filter_input_text = ""
            return True

        elif ch.name == "KEY_ESCAPE":
            filter_input_mode = False
            filter_input_text = ""
            return True

        elif ch.name == "KEY_BACKSPACE":
            filter_input_text = filter_input_text[:-1]
            return True

        elif ch.isdigit():
            filter_input_text += ch
            return True

        return True  # eat everything in filter mode

    # ---------- NORMAL MODE ----------
    # filter entry
    if ch.lower() == "f":
        filter_input_mode = True
        filter_input_text = ""
        return True
    elif ch == "*":
        filters["type"] = filters["channel"] = filters["control"] = None
        return True

    # ---------- SCROLLING (names + common curses codes) ----------
    if ch.is_sequence or getattr(ch, "code", None) is not None:
        name = getattr(ch, "name", "")
        code = getattr(ch, "code", None)

        # Up
        if name == "KEY_UP" or code == 259:
            if scroll_offset + 1 < len(log_buffer):
                scroll_offset += 1
            return True

        # Down
        if name == "KEY_DOWN" or code == 258:
            if scroll_offset > 0:
                scroll_offset -= 1
            return True

        # Page Up (aliases: KEY_PGUP, KEY_PPAGE), code 339
        if name in ("KEY_PGUP", "KEY_PPAGE") or code == 339:
            scroll_offset = min(len(log_buffer) - 1, scroll_offset + VISIBLE_ROWS_TARGET)
            return True

        # Page Down (aliases: KEY_PGDN, KEY_NPAGE, KEY_PGDOWN), code 338
        if name in ("KEY_PGDN", "KEY_NPAGE", "KEY_PGDOWN") or code == 338:
            scroll_offset = max(0, scroll_offset - VISIBLE_ROWS_TARGET)
            return True

        # Home (code 262) — jump to top (oldest)
        if name in ("KEY_HOME", "KEY_FIND") or code == 262:
            scroll_offset = len(log_buffer)
            return True

        # End (code 360) — jump to bottom (latest)
        if name in ("KEY_END","KEY_SELECT") or code == 360:
            scroll_offset = 0
            return True

    return False  # not handled



# ---------- draw ----------
def draw(state):
    y0 = state.get("y_offset", 3)
    cols = state["cols"]
    rows = state["rows"]

    draw_line(y0, f"--- {PAGE_NAME} ---")

    # filter line
    if filter_input_mode:
        draw_line(y0 + 1, f"[Filter: CC {filter_input_text or '?'}]  (Enter=apply, Esc=cancel)")
    else:
        active = []
        if filters["type"]:
            active.append(filters["type"])
        if filters["channel"]:
            active.append(f"Ch{filters['channel']}")
        if filters["control"]:
            active.append(f"CC{filters['control']}")
        if active:
            draw_line(y0 + 1, "Filter: " + ", ".join(active))
        else:
            draw_line(y0 + 1, "(no filters) — press 'f' to enter CC# filter, '*' to clear")

    # compute visible range
    top = y0 + 2
    bottom = rows - 2
    visible_rows = max(5, min(VISIBLE_ROWS_TARGET, bottom - top))

    if len(log_buffer) == 0:
        draw_line(top, "(no events yet)")
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

    # end marker
    marker = f"  ⟵ offset {scroll_offset}" if scroll_offset else "  ⟵ end of log"
    base = visible[-1] if visible else ""
    gap = 5
    usable_cols = cols - gap
    space = max(0, usable_cols - len(base) - len(marker))
    merged = (base[:usable_cols] + " " * space + marker)[:cols - 1]
    draw_line(last_y, merged)
# ---------- SCROLLING HANDLERS ----------
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


def build_widget(state):
    cols = state["cols"]
    rows = state["rows"]
    if filter_input_mode:
        filter_summary = f"[Filter: CC {filter_input_text or '?'}]  (Enter=apply, Esc=cancel)"
    else:
        active = []
        if filters["type"]:
            active.append(filters["type"])
        if filters["channel"]:
            active.append(f"Ch{filters['channel']}")
        if filters["control"]:
            active.append(f"CC{filters['control']}")
        filter_summary = ("Filter: " + ", ".join(active)) if active else "(no filters) — press 'f' to enter CC# filter, '*' to clear"

    y0 = state.get("y_offset", 3)
    top = y0 + 2
    bottom = rows - 2
    visible_rows = max(5, min(VISIBLE_ROWS_TARGET, bottom - top))

    if len(log_buffer) == 0:
        return EventLogWidget(title=f"--- {PAGE_NAME} ---", filter_summary=filter_summary, entries=["(no events yet)"], marker="")

    start_index = max(0, len(log_buffer) - visible_rows - scroll_offset)
    end_index = start_index + visible_rows
    visible = list(log_buffer)[start_index:end_index]
    marker = f"⟵ offset {scroll_offset}" if scroll_offset else "⟵ end of log"
    return EventLogWidget(
        title=f"--- {PAGE_NAME} ---",
        filter_summary=filter_summary[:cols],
        entries=[v[:cols] for v in visible],
        marker=marker,
    )
