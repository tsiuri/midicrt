# pages/pianoroll.py — Piano roll (C2–C6), multi-channel, block-glyph (no Braille)
BACKGROUND = True
PAGE_ID = 8
PAGE_NAME = "Piano Roll"

import sys, time, threading
from collections import deque
from dataclasses import dataclass
from typing import List
from blessed import Terminal
from midicrt import draw_line
from configutil import load_section, save_section
from ui.model import Column, Line, Segment, Style, TextBlock

term = Terminal()

# -------- Configuration --------
PITCH_LOW_DEFAULT  = 36   # C2
PITCH_HIGH_DEFAULT = 83   # B5
TICKS_PER_COL      = 6    # 24 PPQN -> 4 columns per beat
LEFT_MARGIN        = 10
MAX_VISIBLE_ROWS   = 20
IDLE_SCROLL_BPM    = 120  # scroll speed when transport stopped
OUT_RANGE_HOLD     = 2.5  # seconds to show out-of-range note indicator

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
except Exception:
    pass

try:
    save_section("pianoroll", {
        "pitch_low_default": int(PITCH_LOW_DEFAULT),
        "pitch_high_default": int(PITCH_HIGH_DEFAULT),
        "ticks_per_col": int(TICKS_PER_COL),
        "left_margin": int(LEFT_MARGIN),
        "max_visible_rows": int(MAX_VISIBLE_ROWS),
        "idle_scroll_bpm": float(IDLE_SCROLL_BPM),
        "out_range_hold": float(OUT_RANGE_HOLD),
    })
except Exception:
    pass

# -------- State --------
visible_channels = set(range(1, 17))
active = {}         # {(ch, pitch): velocity}
cols_buf = deque()  # time columns of [(pitch, ch, vel)]
time_cols = 0
pitch_low  = PITCH_LOW_DEFAULT
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
            _shift_if_needed({
                "tick":    mc.tick_counter,
                "running": mc.running,
                "bpm":     mc.bpm,
            })
        except Exception:
            pass
        time.sleep(0.05)  # ~20 Hz

def _ensure_bg():
    global _bg_thread
    if _bg_thread and _bg_thread.is_alive():
        return
    _bg_thread = threading.Thread(target=_bg_loop, daemon=True, name="pianoroll-bg")
    _bg_thread.start()

NOTE_NAMES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]

def _notename(n):
    if n < 0: n = 0
    o = n // 12 - 1
    return f"{NOTE_NAMES[n % 12]}{o}"

# -------- MIDI handlers --------
def _note_on(msg):
    global _last_above, _last_below
    ch = msg.channel + 1
    if msg.velocity > 0:
        active[(ch, msg.note)] = msg.velocity
        # record a recent hit so very short notes render at least 1 column
        try:
            _recent_hits.append((msg.note, ch, msg.velocity, time.time()))
        except Exception:
            pass
        # out-of-range indicators
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
    """Scroll the roll buffer based on MIDI clock when running, or
    wall-clock time when stopped so it always moves."""
    global _last_tick, _last_time, _last_run_bpm

    now = time.time()
    running = state.get("running", False)
    bpm = state.get("bpm", 0.0) or 0.0

    # Initialize time on first call
    if _last_time is None:
        _last_time = now
        if bpm > 0:
            _last_run_bpm = bpm

    steps = 0
    if running:
        tick = state["tick"]
        # handle transport restart (tick reset)
        if tick < _last_tick:
            _last_tick = tick
        moved = tick - _last_tick
        if moved >= TICKS_PER_COL:
            steps = max(1, moved // TICKS_PER_COL)
            # advance baseline by the number of columns we produced
            _last_tick += steps * TICKS_PER_COL
        _last_time = now
        if bpm > 0:
            _last_run_bpm = bpm
    else:
        # Advance using wall clock at the last known BPM (or default)
        eff_bpm = _last_run_bpm if _last_run_bpm else IDLE_SCROLL_BPM
        ticks_per_sec = (eff_bpm * 24.0) / 60.0
        elapsed = now - _last_time
        virtual_ticks = elapsed * ticks_per_sec
        if virtual_ticks >= TICKS_PER_COL:
            steps = max(1, int(virtual_ticks // TICKS_PER_COL))
            # consume only the ticks we produced columns for
            consumed = steps * TICKS_PER_COL / ticks_per_sec
            _last_time += consumed

    # determine a conservative overlay window (~1 column worth of time)
    try:
        col_secs = (TICKS_PER_COL / ((bpm if running and bpm > 0 else _last_run_bpm) * 24.0 / 60.0)) if (bpm or _last_run_bpm) else 0.125
    except Exception:
        col_secs = 0.125
    overlay_window = max(0.05, min(0.25, col_secs))

    for _ in range(steps):
        # base events = currently active notes snapshot
        now_col = [(p, ch, v) for (ch, p), v in active.items()]
        # include very recent hits so short blips persist into stored columns
        try:
            cutoff = time.time() - overlay_window
            recent = [(p, ch, v) for (p, ch, v, ts) in list(_recent_hits) if ts >= cutoff]
            if recent:
                now_col.extend(recent)
        except Exception:
            pass
        cols_buf.append(now_col)
    # Do not clear _recent_hits on scroll; keep time-based filtering so
    # blips that straddle a scroll boundary still render at least once.

# -------- Formatting helpers --------
def _merge_left_right(left, right, cols):
    if not right:
        return left[:cols]
    space = cols - len(left) - len(right)
    if space >= 1:
        return (left + (" " * space) + right)[:cols]
    # not enough space; trim left
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


@dataclass(frozen=True)
class FrameSnapshot:
    header_left: str
    header_right: str
    status_line: str
    timeline_line: str
    pitch_lines: List[str]
    footer_left: str
    footer_right: str
    cols: int
    rows: int
    y0: int


def _vel_char(v):
    if v >= 100:
        return "█"
    if v >= 60:
        return "▓"
    if v > 0:
        return "░"
    return " "


def _collect_out_of_range(now, lo, hi, current_above, current_below):
    last_above = current_above
    last_below = current_below
    above_pitches = set()
    below_pitches = set()
    for (ch, pitch), _vel in active.items():
        if pitch > hi:
            last_above = (pitch, ch, now)
            above_pitches.add(pitch)
        elif pitch < lo:
            last_below = (pitch, ch, now)
            below_pitches.add(pitch)
    for (pitch, ch, _vel, ts) in list(_recent_hits):
        if (now - ts) > OUT_RANGE_HOLD:
            continue
        if pitch > hi:
            last_above = (pitch, ch, ts)
            above_pitches.add(pitch)
        elif pitch < lo:
            last_below = (pitch, ch, ts)
            below_pitches.add(pitch)
    return last_above, last_below, above_pitches, below_pitches


def get_view_payload(max_active_notes=64, max_recent_hits=32):
    """Compact normalized view payload for schema snapshots."""
    now = time.time()

    active_notes_payload = [
        [ch, pitch, vel]
        for (ch, pitch), vel in sorted(active.items(), key=lambda item: (item[0][1], item[0][0]))[:max_active_notes]
    ]

    recent_hits = []
    for pitch, ch, vel, ts in list(_recent_hits)[-max_recent_hits:]:
        age_ms = int(max(0.0, now - ts) * 1000.0)
        recent_hits.append([pitch, ch, vel, age_ms])

    overflow_flags = {
        "above": _last_above is not None,
        "below": _last_below is not None,
    }

    return {
        "time_cols": int(time_cols),
        "pitch_low": int(pitch_low),
        "pitch_high": int(pitch_high),
        "active_notes": active_notes_payload,
        "recent_hits": recent_hits,
        "overflow_flags": overflow_flags,
    }


def build_frame_snapshot(state):
    """Deterministic logical frame snapshot for page 8.

    Optional state override: state['_now'] for deterministic tests/rendering.
    """
    global pitch_low, pitch_high, _last_above, _last_below

    cols = state["cols"]
    rows = state["rows"]
    y0 = state.get("y_offset", 3)
    now = state.get("_now", time.time())

    top = y0 + 2
    bottom = rows - 5
    avail_rows = bottom - top
    marker_rows = 1
    total_rows = max(9, avail_rows)
    note_rows = max(1, total_rows - marker_rows)
    pitch_high = pitch_low + note_rows - 1

    _last_above, _last_below, above_pitches, below_pitches = _collect_out_of_range(
        now, pitch_low, pitch_high, _last_above, _last_below
    )

    header_left = f"--- {PAGE_NAME} ---"
    header_right = _fmt_out_of_range(_last_above, now, "high", extra=max(0, len(above_pitches) - 1))
    if vis_input_mode:
        status_line = f"[Channels: {vis_input_text or '?'}] Enter=apply Esc=cancel".ljust(cols)
    else:
        status_line = _channel_legend()[:cols]

    roll_cols = max(16, cols - LEFT_MARGIN - 2)
    _ensure_cols(roll_cols)
    _ensure_bg()

    visible_cols = (
        [[] for _ in range(roll_cols - len(cols_buf))] + list(cols_buf)
        if len(cols_buf) < roll_cols else list(cols_buf)[-roll_cols:]
    )

    if visible_cols:
        overlay = [(p, ch, v) for (p, ch, v, ts) in list(_recent_hits) if (now - ts) <= 0.25]
        if overlay:
            visible_cols[-1] = list(visible_cols[-1]) + overlay

    tick_right = _last_tick
    bar_ticks = 24 * 4
    beat_ticks = 24
    timeline_chars = []
    for i in range(roll_cols):
        col_tick = tick_right - (roll_cols - 1 - i) * TICKS_PER_COL
        if col_tick % bar_ticks == 0:
            timeline_chars.append("|")
        elif col_tick % beat_ticks == 0:
            timeline_chars.append(":")
        else:
            timeline_chars.append(" ")
    timeline_line = (f"{'Bars':>7} │" + "".join(timeline_chars).ljust(roll_cols)[:roll_cols])[:cols]

    pitch_lines = []
    for pitch in range(pitch_high, pitch_low - 1, -1):
        label = f"{_notename(pitch):>7} │"
        chars = []
        for col_events in visible_cols:
            hit_vel = 0
            for (p, ch, v) in col_events:
                if p == pitch and ch in visible_channels:
                    hit_vel = max(hit_vel, v)
            chars.append(_vel_char(hit_vel))
        row = label + "".join(chars).ljust(roll_cols)[:roll_cols] + "│"
        pitch_lines.append(row[:cols])

    footer_left = (
        f"Range: {_notename(pitch_low)}–{_notename(pitch_high)}  "
        f"T/col:{TICKS_PER_COL}  Active:{len(active)}  Cols:{len(cols_buf)}"
    )
    footer_right = _fmt_out_of_range(_last_below, now, "low", extra=max(0, len(below_pitches) - 1))

    return FrameSnapshot(
        header_left=header_left,
        header_right=header_right,
        status_line=status_line,
        timeline_line=timeline_line,
        pitch_lines=pitch_lines,
        footer_left=footer_left,
        footer_right=footer_right,
        cols=cols,
        rows=rows,
        y0=y0,
    )


def _line_with_right_reverse(left, right, cols):
    base = _merge_left_right(left, right, cols)
    if not right:
        return Line.plain(base)
    right = right[-cols:]
    x = max(0, cols - len(right))
    return Line(segments=[
        Segment(text=base[:x]),
        Segment(text=right, style=Style(reverse=True)),
        Segment(text=base[x + len(right):]),
    ])


def build_widget(state):
    snap = build_frame_snapshot(state)
    lines = [
        _line_with_right_reverse(snap.header_left, snap.header_right, snap.cols),
        Line.plain(snap.status_line),
        Line.plain(snap.timeline_line),
    ] + [Line.plain(row) for row in snap.pitch_lines] + [
        _line_with_right_reverse(snap.footer_left, snap.footer_right, snap.cols)
    ]
    return Column(children=[TextBlock(lines=lines)])

# -------- Drawing --------
def draw(state):
    snap = build_frame_snapshot(state)
    draw_line(snap.y0, _merge_left_right(snap.header_left, snap.header_right, snap.cols))
    _draw_right_reverse(snap.y0, snap.header_right, snap.cols)
    draw_line(snap.y0 + 1, snap.status_line)
    draw_line(snap.y0 + 2, snap.timeline_line)

    for i, row in enumerate(snap.pitch_lines):
        draw_line(snap.y0 + 3 + i, row)

    footer_y = snap.rows - 5
    sys.stdout.write(term.move_yx(footer_y, 0))
    sys.stdout.write(term.clear_eol)
    draw_line(footer_y, _merge_left_right(snap.footer_left, snap.footer_right, snap.cols))
    _draw_right_reverse(footer_y, snap.footer_right, snap.cols)
