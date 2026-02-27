# pages/pianoroll_exp.py -- Experimental piano roll with optional paged memory mode
BACKGROUND = True
PAGE_ID = 16
PAGE_NAME = "Piano Roll Exp"

import os
import sys
import time

from blessed import Terminal

from configutil import load_section, save_section
from engine.memory.session_model import SessionModel
import midicrt as mc
from midicrt import draw_line
from ui.model import Column, Line, PianoRollCell, PianoRollWidget, Spacer, TextBlock

import pages.pianoroll as base

term = Terminal()

# -----------------------------------------------------------------------------
# Memory-mode configuration
# -----------------------------------------------------------------------------
_MEMORY_TOGGLE_KEY = "h"
_MEMORY_MAX_SESSIONS = 32
_MEMORY_EXPORT_DIR = "captures/pianoroll_exp"
_MEMORY_LIBRARY_DIR = ""
_CC_LANE_MAX_RATIO = 0.25

_cfg = load_section("pianoroll_exp")
if _cfg is None:
    _cfg = {}
try:
    _MEMORY_MAX_SESSIONS = int(_cfg.get("memory_max_sessions", _MEMORY_MAX_SESSIONS))
    _MEMORY_EXPORT_DIR = str(_cfg.get("memory_export_dir", _MEMORY_EXPORT_DIR))
    _MEMORY_LIBRARY_DIR = str(_cfg.get("memory_library_dir", _MEMORY_LIBRARY_DIR))
    _CC_LANE_MAX_RATIO = float(_cfg.get("cc_lane_max_ratio", _CC_LANE_MAX_RATIO))
except Exception:
    pass

try:
    save_section(
        "pianoroll_exp",
        {
            "memory_max_sessions": int(max(1, _MEMORY_MAX_SESSIONS)),
            "memory_export_dir": str(_MEMORY_EXPORT_DIR),
            "memory_library_dir": str(_MEMORY_LIBRARY_DIR),
            "cc_lane_max_ratio": float(max(0.0, min(0.5, _CC_LANE_MAX_RATIO))),
        },
    )
except Exception:
    pass


# -----------------------------------------------------------------------------
# Session memory state
# -----------------------------------------------------------------------------
_memory_mode = False
_last_running = False
_last_tick = 0

_view_session_idx = -1
_view_page_idx = 0
_last_roll_cols = 16
_memory_status = ""
_view_session_id = ""


def _engine():
    eng = getattr(mc, "ENGINE", None)
    return eng


def _memory_list() -> list[dict]:
    eng = _engine()
    if eng is None:
        return []
    try:
        return eng.memory_list()
    except Exception:
        return []


def _memory_get(session_id: str) -> SessionModel | None:
    eng = _engine()
    if eng is None:
        return None
    try:
        return eng.memory_get(session_id)
    except Exception:
        return None

def _roll_cols_from_screen_cols(cols: int) -> int:
    return max(16, int(cols) - int(base.LEFT_MARGIN) - 2)


def _ticks_per_page(roll_cols: int) -> int:
    """Bar-aligned memory pages so markers stay fixed across pages."""
    tpc = max(1, int(base.TICKS_PER_COL))
    bar_ticks = 24 * 4
    raw = max(1, int(roll_cols)) * tpc
    page_ticks = (raw // bar_ticks) * bar_ticks
    if page_ticks < bar_ticks:
        page_ticks = bar_ticks
    return int(page_ticks)


def _session_page_origin(start_tick: int) -> int:
    bar_ticks = 24 * 4
    st = max(0, int(start_tick))
    return int((st // bar_ticks) * bar_ticks)


def _session_page_count(session: SessionModel, roll_cols: int) -> int:
    page_ticks = _ticks_per_page(roll_cols)
    start = int(session.header.start_tick)
    origin = _session_page_origin(start)
    stop = int(session.header.stop_tick)
    if session.note_spans:
        stop = max(stop, max(int(s.end_tick) for s in session.note_spans))
    if session.active_notes:
        stop = max(stop, max(int(v[0]) for v in session.active_notes.values()))
    dur = max(1, stop - origin + int(base.TICKS_PER_COL))
    return max(1, (dur + page_ticks - 1) // page_ticks)


def _selected_session(running: bool) -> tuple[SessionModel | None, bool]:
    global _view_session_idx, _view_session_id
    current = _memory_get("current")
    if running and current is not None:
        return current, True

    sessions = _memory_list()
    if not sessions:
        _view_session_idx = -1
        _view_session_id = ""
        return None, False

    ids = [str(item.get("id", "")) for item in sessions if str(item.get("id", ""))]
    if _view_session_id and _view_session_id in ids:
        _view_session_idx = ids.index(_view_session_id)
    if _view_session_idx < 0 or _view_session_idx >= len(ids):
        _view_session_idx = len(ids) - 1
    _view_session_id = ids[_view_session_idx]
    return _memory_get(_view_session_id), False


def _build_layout_window(state: dict) -> tuple[int, int, int, int, int]:
    cols = int(state["cols"])
    rows = int(state["rows"])
    y0 = int(state.get("y_offset", 3))
    top = y0 + 2
    bottom = rows - 5
    avail_rows = bottom - top
    total_rows = max(9, avail_rows)
    return cols, rows, y0, top, total_rows


def _recent_overflow(session: SessionModel, pitch_low: int, pitch_high: int) -> tuple[str, str]:
    now = time.time()
    hold = float(base.OUT_RANGE_HOLD)
    recent = [r for r in session.recent_hits if (now - float(r[3])) <= hold]
    above = [r for r in recent if int(r[0]) > pitch_high]
    below = [r for r in recent if int(r[0]) < pitch_low]

    for (ch, note), (_start, vel) in session.active_notes.items():
        if int(note) > pitch_high:
            above.append((int(note), int(ch), int(vel), now))
        elif int(note) < pitch_low:
            below.append((int(note), int(ch), int(vel), now))

    def _fmt(items):
        if not items:
            return ""
        note, ch, _vel, _ts = items[-1]
        extra = max(0, len({int(i[0]) for i in items}) - 1)
        return base._fmt_out_of_range((int(note), int(ch), float(now)), now, "", extra=extra)

    return _fmt(above), _fmt(below)


def _session_cc_tracks(session: SessionModel) -> list[dict]:
    tracks: dict[tuple[int, int], dict] = {}
    for tick, ch, cc, value, ts in session.cc_events:
        key = (int(ch), int(cc))
        ent = tracks.get(key)
        if ent is None:
            ent = {
                "ch": int(ch),
                "cc": int(cc),
                "last_tick": int(tick),
                "last_ts": float(ts),
                "events": [],
            }
            tracks[key] = ent
        ent["events"].append((int(tick), int(value)))
        if int(tick) >= int(ent["last_tick"]):
            ent["last_tick"] = int(tick)
            ent["last_ts"] = float(ts)
    order = session.cc_order

    out: list[dict] = []
    used: set[tuple[int, int]] = set()
    for key in order:
        if not (isinstance(key, (list, tuple)) and len(key) >= 2):
            continue
        k = (int(key[0]), int(key[1]))
        ent = tracks.get(k)
        if ent is not None:
            out.append(ent)
            used.add(k)

    # Fallback for unordered legacy sessions.
    for k, ent in tracks.items():
        if k not in used:
            out.append(ent)
    return out


def _cc_value_char(value: int) -> str:
    # ASCII-only intensity ramp (low->high)
    ramp = ".:-=+*#%@"
    v = max(0, min(127, int(value)))
    idx = int(round((v / 127.0) * (len(ramp) - 1)))
    return ramp[idx]


def _build_cc_lane_lines(
    tracks: list[dict],
    *,
    cc_rows: int,
    roll_cols: int,
    cols: int,
    page_start: int,
    page_end_excl: int,
    tpc: int,
) -> list[str]:
    if cc_rows <= 0 or not tracks:
        return []
    lines: list[str] = []
    for tr in tracks[:cc_rows]:
        graph = [" "] * max(1, roll_cols)
        for tick, value in tr.get("events", []):
            tt = int(tick)
            if tt < page_start or tt >= page_end_excl:
                continue
            ci = (tt - page_start) // max(1, int(tpc))
            if 0 <= ci < len(graph):
                graph[ci] = _cc_value_char(int(value))
        label = f"CC{int(tr['cc']):03d}:{int(tr['ch']):02d} |"
        lines.append((label + "".join(graph))[:cols])
    return lines


def _build_cc_lanes(
    tracks: list[dict],
    *,
    cc_rows: int,
    roll_cols: int,
    page_start: int,
    page_end_excl: int,
    tpc: int,
) -> list[dict]:
    if cc_rows <= 0 or not tracks:
        return []
    lanes: list[dict] = []
    for tr in tracks[:cc_rows]:
        values = [-1] * max(1, roll_cols)
        for tick, value in tr.get("events", []):
            tt = int(tick)
            if tt < page_start or tt >= page_end_excl:
                continue
            ci = (tt - page_start) // max(1, int(tpc))
            if 0 <= ci < len(values):
                values[ci] = max(0, min(127, int(value)))
        lanes.append({
            "cc": int(tr.get("cc", 0)),
            "ch": int(tr.get("ch", 1)),
            "values": values,
        })
    return lanes


def _project_session_page(
    session: SessionModel,
    roll_cols: int,
    pitch_low: int,
    pitch_high: int,
    page_start: int,
    page_end_excl: int,
    include_live_active: bool,
) -> tuple[list[list[tuple[int, int, int]]], list[tuple[int, int, int, int, int]], int]:
    tpc = max(1, int(base.TICKS_PER_COL))
    best_cols = [{} for _ in range(max(1, roll_cols))]
    spans_for_render = []

    spans = [(s.start_tick, s.end_tick, s.pitch, s.channel, s.velocity) for s in session.note_spans]
    if include_live_active:
        now_tick = int(max(page_start, _last_tick))
        for (ch, note), (start, vel) in session.active_notes.items():
            spans.append((int(start), int(now_tick), int(note), int(ch), int(vel)))

    for start, end, pitch, ch, vel in spans:
        pitch = int(pitch)
        ch = int(ch)
        vel = int(vel)
        if ch not in base.visible_channels:
            continue
        if pitch < int(pitch_low) or pitch > int(pitch_high):
            continue

        s = int(start)
        e = int(end)
        if e < s:
            e = s
        if e < page_start or s >= page_end_excl:
            continue

        clip_s = max(s, page_start)
        clip_e = min(e, page_end_excl - 1)
        if clip_e < clip_s:
            continue

        c0 = max(0, min(roll_cols - 1, (clip_s - page_start) // tpc))
        c1 = max(0, min(roll_cols - 1, (clip_e - page_start) // tpc))
        for c in range(c0, c1 + 1):
            prev = best_cols[c].get(pitch)
            if prev is None or vel >= int(prev[1]):
                best_cols[c][pitch] = (ch, vel)

        spans_for_render.append((clip_s, clip_e + 1, pitch, ch, vel))

    columns = [[(p, ch, vel) for p, (ch, vel) in col.items()] for col in best_cols]
    return columns, spans_for_render, int(len(session.active_notes) if include_live_active else 0)


def _build_live_view(state: dict, build_grid: bool) -> dict:
    view = base.build_roll_view(state, build_grid=build_grid)
    view["header_left"] = f"--- {PAGE_NAME} (live) ---"
    if not view.get("input_mode"):
        view["legend"] = f"{base._channel_legend()} [h=mem]"
    return view


def _build_memory_view(state: dict, build_grid: bool) -> dict:
    global _view_page_idx, _last_roll_cols

    cols, rows, y0, top, total_rows = _build_layout_window(state)
    roll_cols = _roll_cols_from_screen_cols(cols)
    _last_roll_cols = roll_cols
    running = bool(state.get("running", False))
    tick_now = int(state.get("tick", _last_tick))

    sessions_meta = _memory_list()
    session, live = _selected_session(running=running)

    timeline = " " * roll_cols
    columns = [[] for _ in range(roll_cols)]
    spans = []
    active_count = 0
    cc_lines: list[str] = []
    cc_lanes: list[dict] = []
    header_right = "MEM empty"
    footer_right = ""
    page_idx = 0
    page_count = 1
    page_start = int(tick_now)
    page_end_excl = page_start + max(1, roll_cols * int(base.TICKS_PER_COL))
    cc_tracks: list[dict] = []
    cc_rows = 0
    marker_rows = 1

    if session is not None:
        page_ticks = _ticks_per_page(roll_cols)
        page_origin = _session_page_origin(int(session.header.start_tick))
        page_count = _session_page_count(session, roll_cols)

        if live:
            page_idx = max(0, (tick_now - page_origin) // page_ticks)
            _view_page_idx = page_idx
        else:
            page_idx = max(0, min(_view_page_idx, page_count - 1))
            _view_page_idx = page_idx

        page_start = int(page_origin + page_idx * page_ticks)
        page_end_excl = page_start + page_ticks
        cc_tracks = _session_cc_tracks(session)

        marks = []
        for i in range(roll_cols):
            col_tick = page_start + (i * int(base.TICKS_PER_COL))
            if col_tick % (24 * 4) == 0:
                marks.append("|")
            elif col_tick % 24 == 0:
                marks.append(":")
            else:
                marks.append(" ")
        timeline = "".join(marks)

        sess_pos = f"S{(_view_session_idx + 1) if (_view_session_idx >= 0) else len(sessions_meta)}"
        if live:
            header_right = f"REC {sess_pos}  P{page_idx + 1}/{page_count}"
        else:
            header_right = f"MEM {sess_pos}/{len(sessions_meta)}  P{page_idx + 1}/{page_count}"

    cc_rows_cap = int(max(0.0, min(0.5, float(_CC_LANE_MAX_RATIO))) * float(total_rows))
    cc_rows_cap = max(0, cc_rows_cap)
    if cc_tracks and cc_rows_cap > 0:
        cc_rows = min(len(cc_tracks), cc_rows_cap)
    else:
        cc_rows = 0

    note_rows = max(1, total_rows - marker_rows - cc_rows)
    base.pitch_high = base.pitch_low + note_rows - 1
    pitches = list(range(base.pitch_high, base.pitch_low - 1, -1))

    if session is not None:
        columns, spans, active_count = _project_session_page(
            session,
            roll_cols=roll_cols,
            pitch_low=int(base.pitch_low),
            pitch_high=int(base.pitch_high),
            page_start=page_start,
            page_end_excl=page_end_excl,
            include_live_active=live,
        )
        _above_txt, below_txt = _recent_overflow(session, int(base.pitch_low), int(base.pitch_high))
        footer_right = below_txt or ""
        if not live and session.export_path:
            footer_right = os.path.basename(str(session.export_path))

    if session is not None and cc_rows > 0:
        cc_lanes = _build_cc_lanes(
            cc_tracks,
            cc_rows=cc_rows,
            roll_cols=roll_cols,
            page_start=page_start,
            page_end_excl=page_end_excl,
            tpc=int(base.TICKS_PER_COL),
        )
        cc_lines = _build_cc_lane_lines(
            cc_tracks,
            cc_rows=cc_rows,
            roll_cols=roll_cols,
            cols=cols,
            page_start=page_start,
            page_end_excl=page_end_excl,
            tpc=int(base.TICKS_PER_COL),
        )

    grid = []
    if build_grid:
        for pitch in pitches:
            row_cells = []
            for col in columns:
                best = None
                for p, ch, vel in col:
                    if int(p) == int(pitch):
                        if best is None or int(vel) >= int(best[1]):
                            best = (ch, vel)
                if best is None:
                    row_cells.append(PianoRollCell())
                else:
                    row_cells.append(PianoRollCell(velocity=int(best[1]), channel=int(best[0])))
            grid.append(row_cells)

    header_left = f"--- {PAGE_NAME} (memory) ---"
    if base.vis_input_mode:
        legend = f"[Channels: {base.vis_input_text or '?'}] Enter=apply Esc=cancel"
    else:
        legend = f"{base._channel_legend()} [h=live, left/right=page, ,/.=session]"

    footer_left = (
        f"Range: {base._notename(base.pitch_low)}-{base._notename(base.pitch_high)}  "
        f"T/col:{base.TICKS_PER_COL}  Active:{active_count}  Cols:{roll_cols}  Mem:{len(sessions_meta)}"
    )
    if _memory_status:
        footer_left = f"{footer_left}  {_memory_status}"

    return {
        "cols": cols,
        "rows": rows,
        "y0": y0,
        "top": top,
        "note_rows": note_rows,
        "roll_cols": roll_cols,
        "header_left": header_left,
        "header_right": header_right,
        "legend": legend,
        "input_mode": bool(base.vis_input_mode),
        "input_text": str(base.vis_input_text),
        "timeline": timeline,
        "pitches": pitches,
        "grid": grid,
        "columns": columns,
        "spans": spans,
        "cc_lanes": cc_lanes,
        "cc_lines": cc_lines,
        "tick_right": int(session.header.start_tick + ((page_idx + 1) * roll_cols - 1) * int(base.TICKS_PER_COL)) if session else int(tick_now),
        "tick_now": int(session.header.start_tick + ((page_idx + 1) * roll_cols - 1) * int(base.TICKS_PER_COL)) if session else int(tick_now),
        "footer_left": footer_left,
        "footer_right": footer_right,
        "overflow": {"above": "", "below": ""},
    }


def _view_to_widget(view: dict) -> Column:
    cols = int(view["cols"])
    header = base._merge_left_right(str(view["header_left"]), str(view["header_right"]), cols)
    info = (
        f"[Channels: {view['input_text'] or '?'}] Enter=apply Esc=cancel"
        if view.get("input_mode")
        else str(view["legend"])
    )
    footer = base._merge_left_right(str(view["footer_left"]), str(view["footer_right"]), cols)
    children = [
        TextBlock(lines=[Line.plain(header), Line.plain(info)]),
        PianoRollWidget(
            pitches=list(view["pitches"]),
            cells=list(view["grid"]),
            columns=list(view["columns"]),
            spans=list(view["spans"]),
            cc_lanes=list(view.get("cc_lanes", [])),
            pitch_low=int(base.pitch_low),
            pitch_high=int(base.pitch_high),
            ticks_per_col=int(base.TICKS_PER_COL),
            tick_right=int(view["tick_right"]),
            tick_now=int(view["tick_now"]),
            timeline=str(view["timeline"]),
            left_margin=int(base.LEFT_MARGIN),
            style_mode=str(base.PIXEL_STYLE),
        ),
    ]
    children.append(Spacer(rows=1))
    children.append(TextBlock(lines=[Line.plain(footer)]))
    return Column(children)


# -----------------------------------------------------------------------------
# Page hooks
# -----------------------------------------------------------------------------
def handle(msg):
    return base.handle(msg)


def on_tick(state):
    global _last_running, _last_tick, _memory_status

    # keep baseline page-8 behavior/state hot in both modes
    base.on_tick(state)

    running = bool(state.get("running", False))
    tick_now = int(state.get("tick", _last_tick))
    _last_tick = tick_now

    status = {}
    eng = _engine()
    if eng is not None:
        try:
            status = eng.memory_status()
        except Exception:
            status = {}
    if status.get("armed") and status.get("current_id"):
        _memory_status = "REC"
    elif _memory_mode:
        _memory_status = "MEM mode"

    _last_running = running


def keypress(ch):
    global _memory_mode, _view_page_idx, _view_session_idx, _view_session_id, _memory_status

    s = str(ch)
    if s.lower() == _MEMORY_TOGGLE_KEY:
        eng = _engine()
        if eng is not None:
            try:
                _memory_mode = bool(eng.memory_toggle())
            except Exception:
                _memory_mode = not _memory_mode
        else:
            _memory_mode = not _memory_mode
        _memory_status = "MEM mode" if _memory_mode else "LIVE mode"
        return True

    # Let channel-visibility edit mode work unchanged.
    if base.vis_input_mode:
        return bool(base.keypress(ch))

    if _memory_mode and (not _last_running):
        sessions = _memory_list()
        if sessions:
            ids = [str(item.get("id", "")) for item in sessions if str(item.get("id", ""))]
            if _view_session_id in ids:
                _view_session_idx = ids.index(_view_session_id)
            elif _view_session_idx < 0 or _view_session_idx >= len(ids):
                _view_session_idx = len(ids) - 1
                _view_session_id = ids[_view_session_idx]
        if s == ",":
            if not sessions:
                return True
            cur = _view_session_idx
            if cur > 0:
                cur -= 1
                _view_session_idx = cur
                _view_session_id = str(sessions[cur].get("id", ""))
                prev_session = _memory_get(_view_session_id)
                if prev_session is not None:
                    prev_pages = max(1, _session_page_count(prev_session, _last_roll_cols))
                    _view_page_idx = min(max(0, int(_view_page_idx)), prev_pages - 1)
            return True
        if s == ".":
            if not sessions:
                return True
            cur = _view_session_idx
            if cur < (len(sessions) - 1):
                _view_session_idx = cur + 1
                _view_session_id = str(sessions[_view_session_idx].get("id", ""))
                next_session = _memory_get(_view_session_id)
                if next_session is not None:
                    next_pages = max(1, _session_page_count(next_session, _last_roll_cols))
                    _view_page_idx = min(max(0, int(_view_page_idx)), next_pages - 1)
            return True
        if ch.is_sequence and ch.name == "KEY_LEFT":
            _view_page_idx = max(0, int(_view_page_idx) - 1)
            return True
        if ch.is_sequence and ch.name == "KEY_RIGHT":
            if sessions and _view_session_id:
                current_session = _memory_get(_view_session_id)
                if current_session is not None:
                    page_count = max(1, _session_page_count(current_session, _last_roll_cols))
                    _view_page_idx = min(page_count - 1, int(_view_page_idx) + 1)
            return True

    # Fall back to normal piano-roll controls (range pan, style toggle, visibility, etc.)
    return bool(base.keypress(ch))


def build_widget(state):
    use_sparse = state.get("render_backend") == "compositor"
    if _memory_mode:
        view = _build_memory_view(state, build_grid=not use_sparse)
    else:
        view = _build_live_view(state, build_grid=not use_sparse)
    return _view_to_widget(view)


def draw(state):
    if not _memory_mode:
        return base.draw(state)

    view = _build_memory_view(state, build_grid=True)
    cols = int(view["cols"])
    rows = int(view["rows"])
    y0 = int(view["y0"])

    draw_line(y0, base._merge_left_right(view["header_left"], view["header_right"], cols))
    base._draw_right_reverse(y0, view["header_right"], cols)
    if view["input_mode"]:
        draw_line(y0 + 1, f"[Channels: {view['input_text'] or '?'}] Enter=apply Esc=cancel".ljust(cols))
    else:
        draw_line(y0 + 1, str(view["legend"])[:cols])

    top = int(view["top"])
    draw_line(top, (f"{'Bars':>7} |" + str(view["timeline"]))[:cols])

    for row, pitch in enumerate(view["pitches"]):
        label = f"{base._notename(int(pitch)):>7} |"
        chars = []
        for cell in view["grid"][row]:
            v = int(cell.velocity)
            if v >= 100:
                chars.append("#")
            elif v >= 60:
                chars.append("+")
            elif v > 0:
                chars.append(".")
            else:
                chars.append(" ")
        draw_line(top + 1 + row, (label + "".join(chars).ljust(view["roll_cols"])[: view["roll_cols"]])[:cols])

    cc_lines = view.get("cc_lines") if isinstance(view.get("cc_lines"), list) else []
    cc_start = top + 1 + len(view["pitches"])
    for i, line in enumerate(cc_lines):
        draw_line(cc_start + i, str(line)[:cols])

    for r in range(int(view["note_rows"]) + 1):
        y = top + r
        sys.stdout.write(term.move_yx(y, int(base.LEFT_MARGIN) + int(view["roll_cols"])) + "|")

    footer_y = rows - 5
    sys.stdout.write(term.move_yx(footer_y, 0))
    sys.stdout.write(term.clear_eol)
    draw_line(footer_y, base._merge_left_right(view["footer_left"], view["footer_right"], cols))
    base._draw_right_reverse(footer_y, view["footer_right"], cols)
