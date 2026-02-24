# pages/pianoroll.py — Piano roll (C2–C6), multi-channel, block-glyph (no Braille)
BACKGROUND = True
PAGE_ID = 8
PAGE_NAME = "Piano Roll"

import sys, time, threading
from collections import deque
from blessed import Terminal
from midicrt import draw_line
from configutil import load_section, save_section
from ui.model import Column, Line, PianoRollCell, PianoRollWidget, Spacer, TextBlock

term = Terminal()

# -------- Configuration --------
PITCH_LOW_DEFAULT = 36  # C2
PITCH_HIGH_DEFAULT = 83  # B5
TICKS_PER_COL = 6  # 24 PPQN -> 4 columns per beat
LEFT_MARGIN = 10
MAX_VISIBLE_ROWS = 20
IDLE_SCROLL_BPM = 120  # scroll speed when transport stopped
OUT_RANGE_HOLD = 2.5  # seconds to show out-of-range note indicator
PIXEL_STYLE = "text"  # text | dense

_cfg = load_section("pianoroll")
if _cfg is None:
    _cfg = {}
try:
    PITCH_LOW_DEFAULT = int(_cfg.get("pitch_low_default", PITCH_LOW_DEFAULT))
    PITCH_HIGH_DEFAULT = int(_cfg.get("pitch_high_default", PITCH_HIGH_DEFAULT))
    TICKS_PER_COL = int(_cfg.get("ticks_per_col", TICKS_PER_COL))
    LEFT_MARGIN = int(_cfg.get("left_margin", LEFT_MARGIN))
    MAX_VISIBLE_ROWS = int(_cfg.get("max_visible_rows", MAX_VISIBLE_ROWS))
    IDLE_SCROLL_BPM = float(_cfg.get("idle_scroll_bpm", IDLE_SCROLL_BPM))
    OUT_RANGE_HOLD = float(_cfg.get("out_range_hold", OUT_RANGE_HOLD))
    PIXEL_STYLE = str(_cfg.get("pixel_style", PIXEL_STYLE)).strip().lower() or "text"
except Exception:
    pass
if PIXEL_STYLE not in {"text", "dense"}:
    PIXEL_STYLE = "text"


def _save_cfg():
    try:
        save_section(
            "pianoroll",
            {
                "pitch_low_default": int(PITCH_LOW_DEFAULT),
                "pitch_high_default": int(PITCH_HIGH_DEFAULT),
                "ticks_per_col": int(TICKS_PER_COL),
                "left_margin": int(LEFT_MARGIN),
                "max_visible_rows": int(MAX_VISIBLE_ROWS),
                "idle_scroll_bpm": float(IDLE_SCROLL_BPM),
                "out_range_hold": float(OUT_RANGE_HOLD),
                "pixel_style": str(PIXEL_STYLE),
            },
        )
    except Exception:
        pass


_save_cfg()

# -------- State --------
visible_channels = set(range(1, 17))
active = {}  # {(ch, pitch): velocity}
cols_buf = deque()  # time columns of [(pitch, ch, vel)]
time_cols = 0
pitch_low = PITCH_LOW_DEFAULT
pitch_high = PITCH_HIGH_DEFAULT
_last_tick = 0
_last_time = None
_recent_hits = deque(maxlen=256)  # [(pitch, ch, vel, ts)] — ensures blips are shown at least once
_last_run_bpm = IDLE_SCROLL_BPM
vis_input_mode = False
vis_input_text = ""
_last_above = None  # (note, ch, ts)
_last_below = None  # (note, ch, ts)

# -------- Background scroll thread --------
_bg_thread = None


def _bg_loop():
    try:
        import midicrt as mc
    except Exception:
        return
    while True:
        try:
            _shift_if_needed(
                {
                    "tick": mc.tick_counter,
                    "running": mc.running,
                    "bpm": mc.bpm,
                }
            )
        except Exception:
            pass
        time.sleep(0.05)  # ~20 Hz


def _ensure_bg():
    global _bg_thread
    if _bg_thread and _bg_thread.is_alive():
        return
    _bg_thread = threading.Thread(target=_bg_loop, daemon=True, name="pianoroll-bg")
    _bg_thread.start()


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _notename(n):
    if n < 0:
        n = 0
    o = n // 12 - 1
    return f"{NOTE_NAMES[n % 12]}{o}"


# -------- MIDI handlers --------
def _note_on(msg):
    global _last_above, _last_below
    ch = msg.channel + 1
    if msg.velocity > 0:
        active[(ch, msg.note)] = msg.velocity
        try:
            _recent_hits.append((msg.note, ch, msg.velocity, time.time()))
        except Exception:
            pass
        if msg.note > pitch_high:
            _last_above = (msg.note, ch, time.time())
        elif msg.note < pitch_low:
            _last_below = (msg.note, ch, time.time())
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
        active.clear()


# -------- Channel visibility --------
def _channel_legend():
    lst = " ".join(str(c) for c in sorted(visible_channels))
    return f"Vis: {lst}   [d=ch10, v=edit, *=all, y=style:{PIXEL_STYLE}]"


def apply_visibility_list(text):
    visible_channels.clear()
    try:
        if not text:
            visible_channels.update(range(1, 17))
            return
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                a, b = map(int, part.split("-", 1))
                lo, hi = min(a, b), max(a, b)
                for c in range(lo, hi + 1):
                    if 1 <= c <= 16:
                        visible_channels.add(c)
            else:
                c = int(part)
                if 1 <= c <= 16:
                    visible_channels.add(c)
        if not visible_channels:
            visible_channels.update(range(1, 17))
    except Exception:
        visible_channels.update(range(1, 17))


def keypress(ch):
    global vis_input_mode, vis_input_text, pitch_low, pitch_high, PIXEL_STYLE
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
        visible_channels.clear()
        visible_channels.update(range(1, 17))
        return True
    if str(ch).lower() == "y":
        PIXEL_STYLE = "dense" if PIXEL_STYLE == "text" else "text"
        _save_cfg()
        return True

    if ch.is_sequence:
        if ch.name in ("KEY_PGUP", "KEY_PPAGE"):
            pitch_low = max(0, pitch_low - 12)
            pitch_high = pitch_low + (pitch_high - pitch_low)
            return True
        elif ch.name in ("KEY_PGDN", "KEY_NPAGE", "KEY_PGDOWN"):
            max_top = 127 - (pitch_high - pitch_low)
            pitch_low = min(max_top, pitch_low + 12)
            pitch_high = pitch_low + (pitch_high - pitch_low)
            return True
        elif ch.name == "KEY_UP":
            max_top = 127 - (pitch_high - pitch_low)
            pitch_low = min(max_top, pitch_low + 1)
            pitch_high = pitch_low + (pitch_high - pitch_low)
            return True
        elif ch.name == "KEY_DOWN":
            pitch_low = max(0, pitch_low - 1)
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
    global _last_tick, _last_time, _last_run_bpm

    now = time.time()
    running = state.get("running", False)
    bpm = state.get("bpm", 0.0) or 0.0

    if _last_time is None:
        _last_time = now
        if bpm > 0:
            _last_run_bpm = bpm

    steps = 0
    if running:
        tick = state["tick"]
        if tick < _last_tick:
            _last_tick = tick
        moved = tick - _last_tick
        if moved >= TICKS_PER_COL:
            steps = max(1, moved // TICKS_PER_COL)
            _last_tick += steps * TICKS_PER_COL
        _last_time = now
        if bpm > 0:
            _last_run_bpm = bpm
    else:
        eff_bpm = _last_run_bpm if _last_run_bpm else IDLE_SCROLL_BPM
        ticks_per_sec = (eff_bpm * 24.0) / 60.0
        elapsed = now - _last_time
        virtual_ticks = elapsed * ticks_per_sec
        if virtual_ticks >= TICKS_PER_COL:
            steps = max(1, int(virtual_ticks // TICKS_PER_COL))
            consumed = steps * TICKS_PER_COL / ticks_per_sec
            _last_time += consumed

    try:
        col_secs = (
            TICKS_PER_COL / ((bpm if running and bpm > 0 else _last_run_bpm) * 24.0 / 60.0)
            if (bpm or _last_run_bpm)
            else 0.125
        )
    except Exception:
        col_secs = 0.125
    overlay_window = max(0.05, min(0.25, col_secs))

    for _ in range(steps):
        now_col = [(p, ch, v) for (ch, p), v in active.items()]
        try:
            cutoff = time.time() - overlay_window
            recent = [(p, ch, v) for (p, ch, v, ts) in list(_recent_hits) if ts >= cutoff]
            if recent:
                now_col.extend(recent)
        except Exception:
            pass
        cols_buf.append(now_col)


# -------- Formatting helpers --------
def _merge_left_right(left, right, cols):
    if not right:
        return left[:cols]
    space = cols - len(left) - len(right)
    if space >= 1:
        return (left + (" " * space) + right)[:cols]
    keep = max(0, cols - len(right) - 1)
    return (left[:keep] + " " + right)[:cols]


def _fmt_out_of_range(last, now, direction, extra=0):
    if not last:
        return ""
    note, ch, ts = last
    if (now - ts) > OUT_RANGE_HOLD:
        return ""
    base = f"{_notename(note)} ch{ch:02d}"
    if extra > 0:
        base += f" (+{extra} more)"
    return base


def _draw_right_reverse(y, text, cols):
    if not text:
        return
    if len(text) > cols:
        text = text[-cols:]
    x = max(0, cols - len(text))
    sys.stdout.write(term.move_yx(y, x) + term.reverse(text) + term.normal)


def _current_roll_view(state):
    global pitch_low, pitch_high, _last_above, _last_below
    cols = state["cols"]
    rows = state["rows"]
    y0 = state.get("y_offset", 3)
    now = time.time()

    top = y0 + 2
    bottom = rows - 5
    avail_rows = bottom - top
    total_rows = max(9, avail_rows)
    marker_rows = 1
    note_rows = max(1, total_rows - marker_rows)
    pitch_high = pitch_low + note_rows - 1

    above_pitches = set()
    below_pitches = set()
    try:
        for (ch, pitch), _vel in active.items():
            if pitch > pitch_high:
                _last_above = (pitch, ch, now)
                above_pitches.add(pitch)
            elif pitch < pitch_low:
                _last_below = (pitch, ch, now)
                below_pitches.add(pitch)
        for (pitch, ch, _vel, ts) in list(_recent_hits):
            if (now - ts) > OUT_RANGE_HOLD:
                continue
            if pitch > pitch_high:
                _last_above = (pitch, ch, ts)
                above_pitches.add(pitch)
            elif pitch < pitch_low:
                _last_below = (pitch, ch, ts)
                below_pitches.add(pitch)
    except Exception:
        pass

    roll_cols = max(16, cols - LEFT_MARGIN - 2)
    _ensure_cols(roll_cols)
    _ensure_bg()

    visible_cols = ([[] for _ in range(roll_cols - len(cols_buf))] + list(cols_buf) if len(cols_buf) < roll_cols else list(cols_buf)[-roll_cols:])

    try:
        tick_right = _last_tick
    except Exception:
        tick_right = state.get("tick", 0)
    bar_ticks = 24 * 4
    beat_ticks = 24
    timeline_chars = []
    for i in range(roll_cols):
        col_tick = tick_right - (roll_cols - 1 - i) * TICKS_PER_COL
        mark = " "
        if col_tick % bar_ticks == 0:
            mark = "|"
        elif col_tick % beat_ticks == 0:
            mark = ":"
        timeline_chars.append(mark)

    if visible_cols:
        now_ts = time.time()
        overlay = [(p, ch, v) for (p, ch, v, ts) in list(_recent_hits) if (now_ts - ts) <= 0.25]
        if overlay:
            visible_cols[-1] = list(visible_cols[-1]) + overlay

    pitches = list(range(pitch_high, pitch_low - 1, -1))
    grid = []
    for pitch in pitches:
        row_cells = []
        for col_events in visible_cols:
            best_vel = 0
            best_ch = None
            for (p, ch, v) in col_events:
                if p == pitch and ch in visible_channels and v >= best_vel:
                    best_vel = v
                    best_ch = ch
            row_cells.append(PianoRollCell(velocity=int(best_vel), channel=best_ch))
        grid.append(row_cells)

    header_left = f"--- {PAGE_NAME} ---"
    header_right = _fmt_out_of_range(_last_above, now, "high", extra=max(0, len(above_pitches) - 1))
    footer_left = f"Range: {_notename(pitch_low)}–{_notename(pitch_high)}  T/col:{TICKS_PER_COL}  Active:{len(active)}  Cols:{len(cols_buf)}"
    footer_right = _fmt_out_of_range(_last_below, now, "low", extra=max(0, len(below_pitches) - 1))

    return {
        "cols": cols,
        "rows": rows,
        "y0": y0,
        "top": top,
        "note_rows": note_rows,
        "roll_cols": roll_cols,
        "header_left": header_left,
        "header_right": header_right,
        "legend": _channel_legend(),
        "input_mode": vis_input_mode,
        "input_text": vis_input_text,
        "timeline": "".join(timeline_chars).ljust(roll_cols)[:roll_cols],
        "pitches": pitches,
        "grid": grid,
        "footer_left": footer_left,
        "footer_right": footer_right,
    }


def build_widget(state):
    view = _current_roll_view(state)
    cols = view["cols"]
    header = _merge_left_right(view["header_left"], view["header_right"], cols)
    header_line = Line.plain(header)

    if view["input_mode"]:
        info = f"[Channels: {view['input_text'] or '?'}] Enter=apply Esc=cancel"
    else:
        info = view["legend"]

    footer = _merge_left_right(view["footer_left"], view["footer_right"], cols)
    footer_line = Line.plain(footer)

    return Column(
        [
            TextBlock(lines=[header_line, Line.plain(info)]),
            PianoRollWidget(
                pitches=view["pitches"],
                cells=view["grid"],
                timeline=view["timeline"],
                left_margin=LEFT_MARGIN,
                style_mode=PIXEL_STYLE,
            ),
            Spacer(rows=1),
            TextBlock(lines=[footer_line]),
        ]
    )


# -------- Drawing --------
def draw(state):
    view = _current_roll_view(state)
    cols = view["cols"]
    rows = view["rows"]
    y0 = view["y0"]

    draw_line(y0, _merge_left_right(view["header_left"], view["header_right"], cols))
    _draw_right_reverse(y0, view["header_right"], cols)
    if view["input_mode"]:
        draw_line(y0 + 1, f"[Channels: {view['input_text'] or '?'}] Enter=apply Esc=cancel".ljust(cols))
    else:
        draw_line(y0 + 1, view["legend"][:cols])

    top = view["top"]
    draw_line(top, (f"{'Bars':>7} │" + view["timeline"])[:cols])

    for row, pitch in enumerate(view["pitches"]):
        label = f"{_notename(pitch):>7} │"
        chars = []
        for cell in view["grid"][row]:
            v = cell.velocity
            if v >= 100:
                chars.append("█")
            elif v >= 60:
                chars.append("▓")
            elif v > 0:
                chars.append("░")
            else:
                chars.append(" ")
        draw_line(top + 1 + row, (label + "".join(chars).ljust(view["roll_cols"])[: view["roll_cols"]])[:cols])

    for r in range(view["note_rows"] + 1):
        y = top + r
        sys.stdout.write(term.move_yx(y, LEFT_MARGIN + view["roll_cols"]) + "│")

    footer_y = rows - 5
    sys.stdout.write(term.move_yx(footer_y, 0))
    sys.stdout.write(term.clear_eol)
    draw_line(footer_y, _merge_left_right(view["footer_left"], view["footer_right"], cols))
    _draw_right_reverse(footer_y, view["footer_right"], cols)
