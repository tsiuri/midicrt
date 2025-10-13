# pages/pianoroll.py — Piano roll (C2–C6), multi-channel, block-glyph (no Braille)
BACKGROUND = True
PAGE_ID = 8
PAGE_NAME = "Piano Roll"

import sys
from collections import deque
from blessed import Terminal
from midicrt import draw_line

term = Terminal()

# -------- Configuration --------
PITCH_LOW_DEFAULT  = 36   # C2
PITCH_HIGH_DEFAULT = 83   # B5
TICKS_PER_COL      = 6    # 24 PPQN -> 4 columns per beat
LEFT_MARGIN        = 10
MAX_VISIBLE_ROWS   = 20

# -------- State --------
visible_channels = set(range(1, 17))
active = {}         # {(ch, pitch): velocity}
cols_buf = deque()  # time columns of [(pitch, ch, vel)]
time_cols = 0
pitch_low  = PITCH_LOW_DEFAULT
pitch_high = PITCH_HIGH_DEFAULT
_last_tick = 0
vis_input_mode = False
vis_input_text = ""

NOTE_NAMES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]

def _notename(n):
    if n < 0: n = 0
    o = n // 12 - 1
    return f"{NOTE_NAMES[n % 12]}{o}"

# -------- MIDI handlers --------
def _note_on(msg):
    ch = msg.channel + 1
    if msg.velocity > 0:
        active[(ch, msg.note)] = msg.velocity
    else:
        active.pop((ch, msg.note), None)

def _note_off(msg):
    ch = msg.channel + 1
    active.pop((ch, msg.note), None)

def _all_notes_off(ch):
    keys = [k for k in active.keys() if k[0] == ch]
    for k in keys:
        active.pop(k, None)

def handle(msg):
    t = msg.type
    if t == "note_on":
        _note_on(msg)
    elif t == "note_off":
        _note_off(msg)
    elif t == "control_change" and msg.control == 123:
        _all_notes_off(msg.channel + 1)
    elif t == "stop":
        active.clear()  # keep running even when stopped, but clear old notes

# -------- Channel visibility --------
def _channel_legend():
    lst = " ".join(str(c) for c in sorted(visible_channels))
    return f"Vis: {lst}   [d=ch10, v=edit, *=all]"

def apply_visibility_list(text):
    visible_channels.clear()
    try:
        if not text:
            visible_channels.update(range(1,17))
            return
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                a,b = map(int, part.split("-",1))
                lo,hi = min(a,b),max(a,b)
                for c in range(lo,hi+1):
                    if 1 <= c <= 16:
                        visible_channels.add(c)
            else:
                c = int(part)
                if 1 <= c <= 16:
                    visible_channels.add(c)
        if not visible_channels:
            visible_channels.update(range(1,17))
    except Exception:
        visible_channels.update(range(1,17))

def keypress(ch):
    global vis_input_mode, vis_input_text, pitch_low, pitch_high
    if vis_input_mode:
        if ch.name == "KEY_ENTER":
            apply_visibility_list(vis_input_text.strip())
            vis_input_mode = False
            vis_input_text = ""
            return True
        elif ch.name == "KEY_ESCAPE":
            vis_input_mode = False
            vis_input_text = ""
            return True
        elif ch.name == "KEY_BACKSPACE":
            vis_input_text = vis_input_text[:-1]
            return True
        else:
            s = str(ch)
            if s and all(c in "0123456789,- " for c in s):
                vis_input_text += s
                return True
            return True

    if str(ch).lower() == "v":
        vis_input_mode = True
        vis_input_text = ""
        return True
    if str(ch).lower() == "d":
        if 10 in visible_channels:
            visible_channels.discard(10)
        else:
            visible_channels.add(10)
        return True
    if str(ch) == "*":
        visible_channels.clear(); visible_channels.update(range(1,17)); return True

    if ch.is_sequence:
        if ch.name in ("KEY_PGUP","KEY_PPAGE"):
            pitch_low  = max(0, pitch_low - 12)
            pitch_high = pitch_low + (pitch_high - pitch_low)
            return True
        elif ch.name in ("KEY_PGDN","KEY_NPAGE","KEY_PGDOWN"):
            max_top = 127 - (pitch_high - pitch_low)
            pitch_low  = min(max_top, pitch_low + 12)
            pitch_high = pitch_low + (pitch_high - pitch_low)
            return True
        elif ch.name == "KEY_HOME":
            pitch_low, pitch_high = PITCH_LOW_DEFAULT, PITCH_HIGH_DEFAULT
            return True
    return False

# -------- Buffer mgmt --------
def _ensure_cols(width_chars):
    global time_cols, cols_buf
    needed = max(16, width_chars)
    if time_cols != needed or not cols_buf:
        time_cols = needed
        cols_buf = deque([[] for _ in range(time_cols)], maxlen=time_cols)

def _shift_if_needed(state):
    """Always scroll, even if transport stopped."""
    global _last_tick
    tick = state["tick"]
    moved = tick - _last_tick
    if moved < TICKS_PER_COL:
        return
    steps = max(1, moved // TICKS_PER_COL)
    _last_tick = tick
    for _ in range(steps):
        now_col = [(p, ch, v) for (ch, p), v in active.items()]
        cols_buf.append(now_col)

# -------- Drawing --------
def draw(state):
    global pitch_low, pitch_high
    cols = state["cols"]
    rows = state["rows"]
    y0   = state.get("y_offset", 3)

    # Header
    draw_line(y0, f"--- {PAGE_NAME} ---".ljust(cols))
    if vis_input_mode:
        draw_line(y0 + 1, f"[Channels: {vis_input_text or '?'}] Enter=apply Esc=cancel".ljust(cols))
    else:
        draw_line(y0 + 1, _channel_legend()[:cols])

    top    = y0 + 2
    bottom = rows - 5
    avail_rows = bottom - top
    semitone_rows = max(8, min(MAX_VISIBLE_ROWS, avail_rows))
    span = semitone_rows
    pitch_high = pitch_low + span - 1

    roll_cols = max(16, cols - LEFT_MARGIN - 2)
    _ensure_cols(roll_cols)
    _shift_if_needed(state)

    # -------- Time columns / note grid --------
    visible_cols = (
        [[] for _ in range(roll_cols - len(cols_buf))] + list(cols_buf)
        if len(cols_buf) < roll_cols else list(cols_buf)[-roll_cols:]
    )

    def vel_char(v):
        if v >= 100: return "█"
        if v >= 60:  return "▓"
        if v >  0:   return "░"
        return " "

    # -------- Draw rows with note labels --------
    for row, pitch in enumerate(range(pitch_high, pitch_low - 1, -1)):
        y = top + row
        label = f"{_notename(pitch):>7} │"  # every semitone labeled
        chars = []
        for col_events in visible_cols:
            hit_vel = 0
            for (p, ch, v) in col_events:
                if p == pitch and ch in visible_channels:
                    hit_vel = max(hit_vel, v)
            chars.append(vel_char(hit_vel))
        full = label + "".join(chars).ljust(roll_cols)[:roll_cols]
        draw_line(y, full[:cols])

    # -------- Right boundary --------
    for r in range(semitone_rows):
        y = top + r
        sys.stdout.write(term.move_yx(y, LEFT_MARGIN + roll_cols) + "│")

    # -------- Footer --------
    footer_y = rows - 5
    sys.stdout.write(term.move_yx(footer_y, 0))
    sys.stdout.write(term.clear_eol)
    status = (
        f"Range: {_notename(pitch_low)}–{_notename(pitch_high)}  "
        f"T/col:{TICKS_PER_COL}  Active:{len(active)}  Cols:{len(cols_buf)}"
    )
    draw_line(footer_y, status[:cols])
