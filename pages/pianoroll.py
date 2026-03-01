# pages/pianoroll.py — Piano roll (C2–C6), multi-channel, block-glyph (no Braille)
BACKGROUND = True
PAGE_ID = 8
PAGE_NAME = "Piano Roll"

import sys, time
from blessed import Terminal
from midicrt import draw_line
from configutil import load_section, save_section
from engine.modules.pianoroll_state import PianoRollState
from engine.memory.tempo_timeline import TempoTimeline
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
PROJECTION_MODE = "beat"  # beat | tempo_relative

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
    PROJECTION_MODE = str(_cfg.get("projection_mode", PROJECTION_MODE)).strip().lower() or "beat"
except Exception:
    pass
if PIXEL_STYLE not in {"text", "dense"}:
    PIXEL_STYLE = "text"
if PROJECTION_MODE not in {"beat", "tempo_relative"}:
    PROJECTION_MODE = "beat"


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
                "projection_mode": str(PROJECTION_MODE),
            },
        )
    except Exception:
        pass


_save_cfg()

# -------- UI state --------
visible_channels = set(range(1, 17))
pitch_low = PITCH_LOW_DEFAULT
pitch_high = PITCH_HIGH_DEFAULT
vis_input_mode = False
vis_input_text = ""

roll_state = PianoRollState(
    ticks_per_col=TICKS_PER_COL,
    idle_scroll_bpm=IDLE_SCROLL_BPM,
    out_range_hold=OUT_RANGE_HOLD,
)


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

_ROLL_VIEW_CACHE = {
    "key": None,
    "timeline": "",
    "best_cols": None,
    "spans": None,
    "grid_key": None,
    "grid": None,
}


def _notename(n):
    if n < 0:
        n = 0
    o = n // 12 - 1
    return f"{NOTE_NAMES[n % 12]}{o}"


# -------- MIDI handlers --------
def handle(msg):
    roll_state.on_midi_event(msg, pitch_low=pitch_low, pitch_high=pitch_high)


def on_tick(state):
    roll_cols = max(16, state["cols"] - LEFT_MARGIN - 2)
    roll_state.on_tick(
        tick=state.get("tick", 0),
        running=state.get("running", False),
        bpm=state.get("bpm", 0.0),
        roll_cols=roll_cols,
        pitch_low=pitch_low,
        pitch_high=pitch_high,
    )


# -------- Channel visibility --------
def _channel_legend():
    lst = " ".join(str(c) for c in sorted(visible_channels))
    return f"Vis: {lst}   [d=ch10, v=edit, *=all, y=style:{PIXEL_STYLE}, p=proj:{PROJECTION_MODE}]"


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
    global vis_input_mode, vis_input_text, pitch_low, pitch_high, PIXEL_STYLE, PROJECTION_MODE
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

    if str(ch).lower() == "p":
        PROJECTION_MODE = "tempo_relative" if PROJECTION_MODE == "beat" else "beat"
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


def get_view_payload(max_active_notes=64, max_recent_hits=32):
    """Compact normalized view payload for schema snapshots."""
    return {
        "pitch_low": int(pitch_low),
        "pitch_high": int(pitch_high),
        **roll_state.get_view_payload(
            pitch_low=pitch_low,
            pitch_high=pitch_high,
            roll_cols=roll_state.time_cols or 16,
            max_active_notes=max_active_notes,
            max_recent_hits=max_recent_hits,
        ),
    }


def _coerce_pianoroll_payload(payload, roll_cols, pitch_low_val, pitch_high_val):
    """Normalize partial/legacy payloads to the views.pianoroll contract."""
    if not isinstance(payload, dict):
        payload = {}
    normalized = {
        "time_cols": int(payload.get("time_cols", roll_cols)),
        "tick_right": int(payload.get("tick_right", 0)),
        "tick_now": int(payload.get("tick_now", payload.get("tick_right", 0))),
        "active_count": int(payload.get("active_count", 0)),
        "pitch_low": int(payload.get("pitch_low", pitch_low_val)),
        "pitch_high": int(payload.get("pitch_high", pitch_high_val)),
        "active_notes": payload.get("active_notes", []),
        "recent_hits": payload.get("recent_hits", []),
        "spans": payload.get("spans", []),
        "overflow_flags": payload.get("overflow_flags", {}),
        "overflow": payload.get("overflow", {}),
        "columns": payload.get("columns", []),
        "projection_mode": str(payload.get("projection_mode", PROJECTION_MODE)),
        "tempo_segments": payload.get("tempo_segments", []),
        "ppqn": int(payload.get("ppqn", 24)) if isinstance(payload, dict) else 24,
        "session_start_tick": int(payload.get("session_start_tick", 0)) if isinstance(payload, dict) else 0,
        "anchor_tick": int(payload.get("anchor_tick", payload.get("tick_now", payload.get("tick_right", 0)))) if isinstance(payload, dict) else 0,
    }
    cols = normalized["columns"] if isinstance(normalized["columns"], list) else []
    if len(cols) < roll_cols:
        cols = ([[] for _ in range(roll_cols - len(cols))] + cols)
    normalized["columns"] = cols[-roll_cols:]
    return normalized


def _payload_from_direct_state(state, roll_cols, now):
    """Backward-compatible adapter from in-process state to views.pianoroll."""
    active_notes = state.get("active_notes")
    if not isinstance(active_notes, dict):
        return None

    payload_active = []
    for channel, notes in active_notes.items():
        try:
            ch = int(channel) + 1
        except Exception:
            continue
        if not isinstance(notes, (set, list, tuple)):
            continue
        for note in notes:
            try:
                payload_active.append([ch, int(note), 100])
            except Exception:
                continue

    columns = [list() for _ in range(roll_cols)]
    for ch, pitch, vel in payload_active:
        columns[-1].append((pitch, ch, vel))

    return {
        "time_cols": roll_cols,
        "tick_right": int(state.get("tick", 0)),
        "tick_now": int(state.get("tick", 0)),
        "active_count": len(payload_active),
        "active_notes": payload_active,
        "recent_hits": [],
        "spans": [],
        "overflow_flags": {"above": False, "below": False},
        "overflow": {
            "above": None,
            "below": None,
            "above_count": 0,
            "below_count": 0,
        },
        "columns": columns,
        "projection_mode": str(PROJECTION_MODE),
        "_source": "direct_state",
        "_adapted_at": float(now),
    }


def _resolve_projection_mode(state):
    views = state.get("views") if isinstance(state.get("views"), dict) else {}
    payload_raw = views.get("pianoroll") or views.get("8")
    if isinstance(payload_raw, dict):
        mode = str(payload_raw.get("projection_mode", "")).strip().lower()
        if mode in {"beat", "tempo_relative"}:
            return mode
    return PROJECTION_MODE


def _project_payload_tempo_relative(payload: dict, *, current_bpm: float) -> dict:
    if not isinstance(payload, dict):
        return payload
    spans = payload.get("spans")
    if not isinstance(spans, list) or not spans:
        return payload

    tempo_segments = payload.get("tempo_segments", [])
    if not isinstance(tempo_segments, list) or not tempo_segments:
        return payload

    try:
        ppqn = int(payload.get("ppqn", 24))
    except Exception:
        ppqn = 24
    session_start_tick = int(payload.get("session_start_tick", payload.get("anchor_tick", 0)))

    seg_pairs = []
    for seg in tempo_segments:
        if not isinstance(seg, dict):
            continue
        try:
            seg_pairs.append((int(seg.get("start_tick", session_start_tick)), float(seg.get("bpm", 120.0))))
        except Exception:
            continue
    if not seg_pairs:
        return payload

    timeline = TempoTimeline(anchor_tick=session_start_tick, ppqn=ppqn, segments=seg_pairs)
    anchor_tick = int(payload.get("anchor_tick", payload.get("tick_now", payload.get("tick_right", 0))))
    bpm = float(current_bpm if current_bpm > 0 else payload.get("current_bpm", 120.0) or 120.0)

    projected_spans = []
    for item in spans:
        if not isinstance(item, (list, tuple)) or len(item) < 5:
            continue
        start, end, pitch, ch, vel = item[:5]
        try:
            s = float(timeline.project_tick(start, current_bpm=bpm, anchor_tick=anchor_tick))
            e = float(timeline.project_tick(end, current_bpm=bpm, anchor_tick=anchor_tick))
            if e < s:
                e = s
        except Exception:
            continue
        out = [int(s), int(e), int(pitch), int(ch), int(vel)]
        if len(item) >= 6:
            out.append(int(item[5]))
        projected_spans.append(out)

    cloned = dict(payload)
    cloned["spans"] = projected_spans
    cloned["tick_now"] = int(anchor_tick)
    cloned["tick_right"] = int(anchor_tick)
    cloned["projection_mode"] = "tempo_relative"
    cloned["projection_anchor_tick"] = int(anchor_tick)
    return cloned


def _resolve_pianoroll_payload(state, roll_cols, pitch_low_val, pitch_high_val, now):
    views = state.get("views") if isinstance(state.get("views"), dict) else {}
    payload_raw = views.get("pianoroll") or views.get("8")
    if payload_raw is None:
        payload_raw = _payload_from_direct_state(state, roll_cols=roll_cols, now=now)
    if payload_raw is None:
        payload_raw = {
            "pitch_low": int(pitch_low_val),
            "pitch_high": int(pitch_high_val),
            "projection_mode": str(PROJECTION_MODE),
            **roll_state.get_view_payload(
                pitch_low=pitch_low_val,
                pitch_high=pitch_high_val,
                roll_cols=roll_cols,
                now=now,
            ),
        }
    payload = _coerce_pianoroll_payload(payload_raw, roll_cols, pitch_low_val, pitch_high_val)
    payload["_cache_marker"] = _payload_cache_marker(payload_raw)
    return payload


def _payload_cache_marker(payload):
    if not isinstance(payload, dict):
        return None
    for key in (
        "_version",
        "version",
        "update_counter",
        "_update_counter",
        "revision",
        "rev",
        "seq",
    ):
        val = payload.get(key)
        if isinstance(val, (int, float, str)):
            return (key, val)
    return ("identity", id(payload))


def _visibility_hash():
    return hash(tuple(sorted(visible_channels)))


def _best_visible_columns(columns):
    """Return per-column maps {pitch: (channel, velocity)} filtered by visibility."""
    best_cols = []
    for col_events in columns:
        best: dict[int, tuple[int, int]] = {}
        for (p, ch, v) in col_events:
            if ch not in visible_channels:
                continue
            prev = best.get(p)
            if prev is None or v >= prev[1]:
                best[p] = (int(ch), int(v))
        best_cols.append(best)
    return best_cols


def build_roll_view(state, build_grid: bool = True):
    """Canonical logical frame view for page 8.

    Optional override: state['_now'] for deterministic tests/rendering.
    """
    global pitch_low, pitch_high
    cols = state["cols"]
    rows = state["rows"]
    y0 = state.get("y_offset", 3)
    now = float(state.get("_now", time.time()))

    top = y0 + 2
    bottom = rows - 5
    avail_rows = bottom - top
    total_rows = max(9, avail_rows)
    marker_rows = 1
    total_rows = max(9, avail_rows)
    note_rows = max(1, total_rows - marker_rows)
    pitch_high = pitch_low + note_rows - 1

    roll_cols = max(16, cols - LEFT_MARGIN - 2)
    payload = _resolve_pianoroll_payload(
        state,
        roll_cols=roll_cols,
        pitch_low_val=pitch_low,
        pitch_high_val=pitch_high,
        now=now,
    )
    projection_mode = _resolve_projection_mode(state)
    if projection_mode == "tempo_relative":
        payload = _project_payload_tempo_relative(payload, current_bpm=float(state.get("bpm", IDLE_SCROLL_BPM) or IDLE_SCROLL_BPM))
    visible_cols = payload["columns"]
    tick_right = payload.get("tick_right", state.get("tick", 0))
    tick_now = payload.get("tick_now", state.get("tick", tick_right))
    bar_ticks = 24 * 4
    beat_ticks = 24
    tick_bucket = int(tick_now) // max(1, TICKS_PER_COL)
    cache_key = (
        tick_bucket,
        int(roll_cols),
        int(pitch_low),
        int(pitch_high),
        _visibility_hash(),
        payload.get("_cache_marker"),
    )

    if _ROLL_VIEW_CACHE["key"] == cache_key:
        timeline = _ROLL_VIEW_CACHE["timeline"]
        best_cols = _ROLL_VIEW_CACHE["best_cols"]
        spans = _ROLL_VIEW_CACHE["spans"]
    else:
        timeline_chars = []
        for i in range(roll_cols):
            col_tick = tick_right - (roll_cols - 1 - i) * TICKS_PER_COL
            mark = " "
            if col_tick % bar_ticks == 0:
                mark = "|"
            elif col_tick % beat_ticks == 0:
                mark = ":"
            timeline_chars.append(mark)
        timeline = "".join(timeline_chars).ljust(roll_cols)[:roll_cols]
        best_cols = _best_visible_columns(visible_cols)
        spans = [
            span for span in payload.get("spans", [])
            if isinstance(span, (list, tuple)) and len(span) >= 5 and span[3] in visible_channels
        ]
        _ROLL_VIEW_CACHE["key"] = cache_key
        _ROLL_VIEW_CACHE["timeline"] = timeline
        _ROLL_VIEW_CACHE["best_cols"] = best_cols
        _ROLL_VIEW_CACHE["spans"] = spans
        _ROLL_VIEW_CACHE["grid_key"] = None
        _ROLL_VIEW_CACHE["grid"] = None

    pitches = list(range(pitch_high, pitch_low - 1, -1))

    grid = []
    if build_grid:
        if _ROLL_VIEW_CACHE["grid_key"] == cache_key:
            grid = _ROLL_VIEW_CACHE["grid"] or []
        else:
            for pitch in pitches:
                row_cells = []
                for col_best in best_cols:
                    match = col_best.get(pitch)
                    if match is None:
                        row_cells.append(PianoRollCell())
                    else:
                        ch, vel = match
                        row_cells.append(PianoRollCell(velocity=int(vel), channel=ch))
                grid.append(row_cells)
            _ROLL_VIEW_CACHE["grid_key"] = cache_key
            _ROLL_VIEW_CACHE["grid"] = grid

    columns = [
        [(pitch, ch, vel) for pitch, (ch, vel) in col_best.items()]
        for col_best in best_cols
    ]

    header_left = f"--- {PAGE_NAME} ---"
    overflow = payload.get("overflow", {})
    header_right = _fmt_out_of_range(overflow.get("above"), now, "high", extra=overflow.get("above_count", 0))
    footer_left = f"Range: {_notename(pitch_low)}–{_notename(pitch_high)}  T/col:{TICKS_PER_COL}  Active:{payload.get('active_count', 0)}  Cols:{payload.get('time_cols', roll_cols)}"
    footer_right = _fmt_out_of_range(overflow.get("below"), now, "low", extra=overflow.get("below_count", 0))

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
        "timeline": timeline,
        "pitches": pitches,
        "grid": grid,
        "columns": columns,
        "spans": spans,
        "tick_right": int(tick_right),
        "tick_now": int(tick_now),
        "projection_mode": str(projection_mode),
        "footer_left": footer_left,
        "footer_right": footer_right,
        "overflow": {
            "above": bool(header_right),
            "below": bool(footer_right),
        },
    }


def build_frame_snapshot(state):
    """Backwards-compatible alias for canonical roll-view assembly."""
    return build_roll_view(state)


def build_widget(state):
    use_sparse = state.get("render_backend") == "compositor"
    view = build_roll_view(state, build_grid=not use_sparse)
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
                columns=view["columns"],
                spans=view["spans"],
                pitch_low=pitch_low,
                pitch_high=pitch_high,
                ticks_per_col=TICKS_PER_COL,
                tick_right=view["tick_right"],
                tick_now=view["tick_now"],
                timeline=view["timeline"],
                left_margin=LEFT_MARGIN,
                style_mode=PIXEL_STYLE,
                projection_mode=view.get("projection_mode", "beat"),
            ),
            Spacer(rows=1),
            TextBlock(lines=[footer_line]),
        ]
    )


# -------- Drawing --------
def draw(state):
    view = build_roll_view(state)
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
            if v >= 96:
                chars.append("█")
            elif v >= 48:
                chars.append("▓")
            elif v > 0:
                chars.append("▒")
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
