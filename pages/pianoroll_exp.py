# pages/pianoroll_exp.py -- Experimental piano roll with optional paged memory mode
BACKGROUND = True
PAGE_ID = 16
PAGE_NAME = "Piano Roll Exp"

import bisect
import os
import sys
import time
from uuid import uuid4

from blessed import Terminal

from configutil import load_section, save_section
from engine.memory.editor import SessionEditor
from engine.memory.session_model import SessionModel
from engine.memory import midi_io, storage
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
_MODE_LIVE = "LIVE"
_MODE_MEM_BROWSER = "MEM_BROWSER"
_MODE_MEM_EDIT = "MEM_EDIT"
_MODE_MEM_PLAYBACK = "MEM_PLAYBACK"

_ui_mode = _MODE_LIVE
_last_running = False
_last_tick = 0

_view_session_idx = -1
_view_page_idx = 0
_last_roll_cols = 16
_memory_status = ""
_view_session_id = ""
_status_level = "info"
_editor_tool = "SELECT"
_editor_selection = {"start": 0, "end": 0, "lane": "notes"}
_editor: SessionEditor | None = None
_editor_source_session_id = ""
_editor_revision_id = "r0"
_editor_dirty = False

# Session data cache — avoids deepcopy on every draw frame
_session_cache: "SessionModel | None" = None
_session_cache_id: str = ""
_session_cache_t: float = 0.0
_sessions_meta_cache: list = []
_sessions_meta_cache_t: float = 0.0
_SESSION_CACHE_TTL = 0.1    # 10 Hz max refresh (halves deepcopy rate; 100 ms active-note lag)

# Derived-data cache: recomputed only when session or roll_cols changes.
# These are O(events/spans) so must NOT run every 60Hz frame.
_derived_session_id: str = ""
_derived_roll_cols: int = 0
_derived_metadata: list = []
_derived_page_count: int = 1
_derived_cc_tracks: list = []
_derived_spans: list = []          # sorted (start, end, pitch, ch, vel) tuples
_derived_span_starts: list = []   # parallel list of start_ticks for bisect
_derived_max_span_dur: int = 0    # max (end-start) across all spans — bisect lower bound
_derived_span_count: int = 0      # len(session.note_spans) at last derive — used to find newly-released notes
_derived_cache_t: float = 0.0     # wall-clock time of last derived refresh
_DERIVED_LIVE_TTL = 0.2           # re-derive at 5 Hz during live so recently-released notes stay visible

# Rate-limit on_tick memory_status poll (5 Hz is plenty)
_status_poll_t: float = 0.0
_STATUS_POLL_TTL = 0.2


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


def _memory_get_current_display() -> "SessionModel | None":
    """Lightweight live-session snapshot — shallow-copies only display-relevant fields,
    skips the ever-growing events list entirely."""
    eng = _engine()
    if eng is None:
        return None
    try:
        return eng.memory_get_current_display()
    except Exception:
        return None


def _memory_get_ref(session_id: str) -> SessionModel | None:
    """Direct reference to a finalized session — no deepcopy, safe for read-only display."""
    eng = _engine()
    if eng is None:
        return None
    try:
        return eng.memory_get_ref(session_id)
    except Exception:
        return None


def _get_cached_session(running: bool) -> "tuple[list[dict], SessionModel | None, bool]":
    """Rate-limited session data fetch (max 20 Hz) with stale-on-stop protection."""
    global _session_cache, _session_cache_id, _session_cache_t
    global _sessions_meta_cache, _sessions_meta_cache_t
    global _view_session_idx, _view_session_id

    now = time.time()

    # Refresh sessions list at TTL cadence.
    if now - _sessions_meta_cache_t >= _SESSION_CACHE_TTL:
        _sessions_meta_cache = _memory_list()
        _sessions_meta_cache_t = now
    sessions_meta = _sessions_meta_cache

    if running:
        # Live recording: use the lightweight display snapshot (skips events list).
        if now - _session_cache_t >= _SESSION_CACHE_TTL or _session_cache_id != "current":
            _session_cache = _memory_get_current_display()
            _session_cache_id = "current"
            _session_cache_t = now
        if _session_cache is not None:
            return sessions_meta, _session_cache, True
        # Memory not capturing — fall through to show most-recent finalized session.

    # Non-running: pick the selected (or most-recent) finalized session.
    ids = [str(item.get("id", "")) for item in sessions_meta if str(item.get("id", ""))]
    if not ids:
        _view_session_idx = -1
        _view_session_id = ""
        _session_cache_id = ""
        # Don't clear _session_cache: it may still hold the live snapshot while
        # _finalize_session() races to add the new session to the index.
        # Returning it keeps notes visible instead of a blank flash.
        return sessions_meta, _session_cache, False

    if _view_session_id and _view_session_id in ids:
        _view_session_idx = ids.index(_view_session_id)
    if _view_session_idx < 0 or _view_session_idx >= len(ids):
        _view_session_idx = len(ids) - 1
    _view_session_id = ids[_view_session_idx]

    target_id = _view_session_id
    if _session_cache_id != target_id:
        # Finalized sessions are immutable — use a direct reference, no deepcopy.
        _session_cache = _memory_get_ref(target_id)
        _session_cache_id = target_id
        _session_cache_t = now
    return sessions_meta, _session_cache, False


def _refresh_derived(session: "SessionModel | None", sessions_meta: list, roll_cols: int) -> None:
    """Recompute O(events/spans) derived data and cache it.

    Called only when session identity or roll_cols changes — never at 60 Hz.
    """
    global _derived_session_id, _derived_roll_cols, _derived_metadata
    global _derived_page_count, _derived_cc_tracks
    global _derived_spans, _derived_span_starts, _derived_max_span_dur, _derived_span_count, _derived_cache_t
    if session is None:
        _derived_session_id = ""
        _derived_roll_cols = int(roll_cols)
        _derived_metadata = _session_metadata(None, sessions_meta)
        _derived_page_count = 1
        _derived_cc_tracks = []
        _derived_spans = []
        _derived_span_starts = []
        _derived_max_span_dur = 0
        _derived_span_count = 0
    else:
        _derived_session_id = str(session.header.session_id)
        _derived_roll_cols = int(roll_cols)
        _derived_metadata = _session_metadata(session, sessions_meta)
        _derived_page_count = _session_page_count(session, roll_cols)
        _derived_cc_tracks = _session_cc_tracks(session)
        # Pre-build sorted span list so _project_session_page can binary-search it.
        spans = sorted(
            ((int(s.start_tick), int(s.end_tick), int(s.pitch), int(s.channel), int(s.velocity))
             for s in session.note_spans),
            key=lambda t: t[0],
        )
        _derived_spans = spans
        _derived_span_starts = [s[0] for s in spans]
        _derived_max_span_dur = max((s[1] - s[0] for s in spans), default=0)
        # Record how many spans were in note_spans at this derive time so
        # _project_session_page can supplement with spans added since then.
        _derived_span_count = len(session.note_spans)
    _derived_cache_t = time.time()


def _roll_cols_from_screen_cols(cols: int) -> int:
    return max(16, int(cols) - int(base.LEFT_MARGIN) - 2)


def _ticks_per_page(roll_cols: int) -> int:
    """Bar-aligned memory pages so markers stay fixed across pages."""
    tpc = max(1, int(base.TICKS_PER_COL))
    bar_ticks = 24 * 4
    raw = max(1, int(roll_cols)) * tpc
    page_ticks = ((raw + bar_ticks - 1) // bar_ticks) * bar_ticks
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
        stop = max(stop, max(int(v[-1][0]) for v in session.active_notes.values() if v))
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
    top = y0 + 12
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

    for (ch, note), stack in session.active_notes.items():
        if not stack:
            continue
        _start, vel = stack[-1]
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
) -> tuple[list[list[tuple[int, int, int]]], list[tuple[int, int, int, int, int, int]], int]:
    tpc = max(1, int(base.TICKS_PER_COL))
    best_cols = [{} for _ in range(max(1, roll_cols))]
    spans_for_render = []

    # Use pre-built sorted span list from the derived cache; fall back to building it.
    # The `not include_live_active` guard has been removed: the derived cache is now
    # refreshed at 5 Hz (_DERIVED_LIVE_TTL = 0.2) even during live recording, so it
    # is fresh enough to binary-search.  Active notes are still appended below.
    use_derived = (_derived_session_id == session.header.session_id and _derived_span_starts)
    if use_derived:
        # Binary-search upper bound: skip spans starting at or after page_end_excl.
        upper = bisect.bisect_left(_derived_span_starts, page_end_excl)
        # Binary-search lower bound: skip spans that ended before page_start.
        # Any span with start < (page_start - max_span_dur) is guaranteed to end before page_start.
        lower_tick = page_start - max(0, _derived_max_span_dur)
        lower = bisect.bisect_left(_derived_span_starts, lower_tick) if lower_tick > 0 else 0
        raw_spans = _derived_spans[lower:upper]
    elif _derived_session_id == session.header.session_id:
        raw_spans = _derived_spans
    else:
        raw_spans = [(int(s.start_tick), int(s.end_tick), int(s.pitch), int(s.channel), int(s.velocity))
                     for s in session.note_spans]
    # Always supplement with spans added to note_spans since the last derive.
    # This covers:
    #   • just-released notes during live recording (was gated on include_live_active before)
    #   • notes flushed from active_notes by _finalize_session() on transport stop
    # When _derived_span_count == len(session.note_spans) the slice is empty, zero cost.
    new_since_derive = [
        (int(s.start_tick), int(s.end_tick), int(s.pitch), int(s.channel), int(s.velocity))
        for s in session.note_spans[_derived_span_count:]
    ]
    if include_live_active:
        now_tick = int(max(page_start, _last_tick))
        extra = [
            (int(start), int(now_tick), int(note), int(ch), int(vel))
            for (ch, note), stack in session.active_notes.items()
            if stack
            for start, vel in (stack[-1],)
        ]
        raw_spans = list(raw_spans) + new_since_derive + extra
    elif new_since_derive:
        raw_spans = list(raw_spans) + new_since_derive

    # Show notes that are still in active_notes but we're not in live mode —
    # this happens in the brief window after transport stop while _finalize_session()
    # hasn't run yet (snapshot still has open active_notes; finalized session clears them).
    # Render them as closed at the last known tick so they don't vanish at stop time.
    # For finalized sessions active_notes is always empty so this is a no-op.
    if not include_live_active and session.active_notes:
        stopped_at = int(_last_tick)
        stopped_notes = [
            (int(start), stopped_at, int(note), int(ch), int(vel))
            for (ch, note), stack in session.active_notes.items()
            if stack
            for start, vel in (stack[-1],)
        ]
        if stopped_notes:
            raw_spans = list(raw_spans) + stopped_notes

    for start, end, pitch, ch, vel in raw_spans:
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

        # Preserve original start tick (s) so compositor overlap detection can
        # distinguish true in-window onsets from spans clipped at page_start.
        spans_for_render.append((clip_s, clip_e + 1, pitch, ch, vel, s))

    columns = [[(p, ch, vel) for p, (ch, vel) in col.items()] for col in best_cols]
    return columns, spans_for_render, int(len(session.active_notes) if include_live_active else 0)


def _build_live_view(state: dict, build_grid: bool) -> dict:
    view = base.build_roll_view(state, build_grid=build_grid)
    view["header_left"] = f"--- {PAGE_NAME} (live) ---"
    if not view.get("input_mode"):
        view["legend"] = f"{base._channel_legend()} [h=mem]"
    return view


def _session_metadata(session: SessionModel | None, sessions_meta: list[dict]) -> list[str]:
    if session is None:
        return [
            "Session: --",
            "Bars: --",
            "Channels: --",
            "Events: note -- / cc -- / pgm --",
            "Source: --",
            "Revision: --",
        ]
    start_tick = int(session.header.start_tick)
    stop_tick = max(int(session.header.stop_tick), start_tick)
    bar_ticks = 24 * 4
    bars = max(1, (max(1, stop_tick - start_tick) + bar_ticks - 1) // bar_ticks)
    event_counts: dict[str, int] = {}
    for ev in session.events:
        kind = str(getattr(ev, "kind", ""))
        event_counts[kind] = event_counts.get(kind, 0) + 1
    channels = sorted({int(getattr(ev, "channel", 0)) for ev in session.events if getattr(ev, "channel", None) is not None})
    source = "captured"
    rev = "r1"
    sid = str(session.header.session_id)
    for idx, row in enumerate(sessions_meta):
        if str(row.get("id", "")) != sid:
            continue
        source = str(row.get("origin", source) or source)
        rev = f"r{idx + 1}"
        break
    return [
        f"Session: {sid}",
        f"Bars: {bars}",
        f"Channels: {','.join(str(c) for c in channels) if channels else '--'}",
        f"Events: note {event_counts.get('note_on', 0)} / cc {event_counts.get('control_change', 0)} / pgm {event_counts.get('program_change', 0)}",
        f"Source: {source}",
        f"Revision: {rev}",
    ]


def _editor_projection(session: SessionModel | None, editor_state: dict, total_rows: int, build_grid: bool) -> dict:
    roll_cols = int(editor_state.get("roll_cols", 16))
    if session is None:
        note_rows = max(1, total_rows - 2)
        base.pitch_high = base.pitch_low + note_rows - 1
        pitches = list(range(base.pitch_high, base.pitch_low - 1, -1))
        return {
            "columns": [[] for _ in range(max(1, roll_cols))],
            "spans": [],
            "cc_lanes": [],
            "cc_lines": [],
            "pitches": pitches,
            "grid": [[PianoRollCell() for _ in range(roll_cols)] for _ in pitches],
            "note_rows": note_rows,
            "active_count": 0,
        }

    cc_tracks = _derived_cc_tracks if _derived_cc_tracks is not None else _session_cc_tracks(session)
    cc_rows_cap = int(max(0.0, min(0.5, float(_CC_LANE_MAX_RATIO))) * float(total_rows))
    cc_rows = min(len(cc_tracks), max(0, cc_rows_cap)) if cc_tracks else 0
    note_rows = max(1, total_rows - 1 - cc_rows)
    base.pitch_high = base.pitch_low + note_rows - 1

    columns, spans, active_count = _project_session_page(
        session,
        roll_cols=roll_cols,
        pitch_low=int(base.pitch_low),
        pitch_high=int(base.pitch_high),
        page_start=int(editor_state.get("page_start", 0)),
        page_end_excl=int(editor_state.get("page_end_excl", 0)),
        include_live_active=bool(editor_state.get("include_live_active", False)),
    )

    cc_lanes = _build_cc_lanes(
        cc_tracks,
        cc_rows=cc_rows,
        roll_cols=roll_cols,
        page_start=int(editor_state.get("page_start", 0)),
        page_end_excl=int(editor_state.get("page_end_excl", 0)),
        tpc=int(base.TICKS_PER_COL),
    )
    cc_lines = _build_cc_lane_lines(
        cc_tracks,
        cc_rows=cc_rows,
        roll_cols=roll_cols,
        cols=int(editor_state.get("cols", 80)),
        page_start=int(editor_state.get("page_start", 0)),
        page_end_excl=int(editor_state.get("page_end_excl", 0)),
        tpc=int(base.TICKS_PER_COL),
    )

    pitches = list(range(base.pitch_high, base.pitch_low - 1, -1))
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

    return {
        "columns": columns,
        "spans": spans,
        "cc_lanes": cc_lanes,
        "cc_lines": cc_lines,
        "pitches": pitches,
        "grid": grid,
        "note_rows": note_rows,
        "active_count": int(active_count),
    }


def _build_memory_view(state: dict, build_grid: bool) -> dict:
    global _view_page_idx, _last_roll_cols

    cols, rows, y0, top, total_rows = _build_layout_window(state)
    roll_cols = _roll_cols_from_screen_cols(cols)
    _last_roll_cols = roll_cols
    running = bool(state.get("running", False))
    tick_now = int(state.get("tick", _last_tick))

    sessions_meta, session, live = _get_cached_session(running)
    if _ui_mode == _MODE_MEM_EDIT and _editor is not None:
        staged = _editor_current_session()
        if staged is not None:
            session = staged
            live = False

    # Refresh derived cache when session identity, roll_cols, or (for live) time changes.
    now_t = time.time()
    sid_now = str(session.header.session_id) if session is not None else ""
    need_derive = (
        sid_now != _derived_session_id
        or roll_cols != _derived_roll_cols
        or (live and now_t - _derived_cache_t >= _DERIVED_LIVE_TTL)
    )
    if need_derive:
        _refresh_derived(session, sessions_meta, roll_cols)

    page_idx = 0
    page_count = _derived_page_count
    page_start = 0   # safe fallback — overwritten below when session is not None
    page_end_excl = max(1, roll_cols * int(base.TICKS_PER_COL))
    timeline = " " * roll_cols

    if session is not None:
        page_ticks = _ticks_per_page(roll_cols)
        page_origin = _session_page_origin(int(session.header.start_tick))
        page_count = _derived_page_count
        if live:
            page_idx = max(0, (tick_now - page_origin) // page_ticks)
            _view_page_idx = page_idx
        else:
            page_idx = max(0, min(_view_page_idx, page_count - 1))
            _view_page_idx = page_idx
        page_start = int(page_origin + page_idx * page_ticks)
        page_end_excl = page_start + page_ticks

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

    projection = _editor_projection(
        session,
        {
            "roll_cols": roll_cols,
            "page_start": page_start,
            "page_end_excl": page_end_excl,
            "include_live_active": live,
            "cols": cols,
        },
        total_rows,
        build_grid,
    )

    mode_label = {
        _MODE_MEM_BROWSER: "browser",
        _MODE_MEM_EDIT: "edit",
        _MODE_MEM_PLAYBACK: "playback",
    }.get(_ui_mode, "memory")
    sess_pos = (_view_session_idx + 1) if (_view_session_idx >= 0) else len(sessions_meta)
    header_right = f"{mode_label.upper()} S{sess_pos}/{max(1, len(sessions_meta))} P{page_idx + 1}/{page_count}"
    header_left = f"--- {PAGE_NAME} ({mode_label}) ---"

    metadata_lines = list(_derived_metadata)

    editor_lines: list[str] = []
    if _ui_mode == _MODE_MEM_EDIT:
        sel_a = int(_editor_selection.get("start", 0))
        sel_b = int(_editor_selection.get("end", 0))
        status_line = f"Tool:{_editor_tool}  Select:{min(sel_a, sel_b)}..{max(sel_a, sel_b)}  Lane:{_editor_selection.get('lane', 'notes')}"
        unsaved = "yes" if _editor_dirty else "no"
        editor_lines = [
            f"Rev:{_editor_revision_id}  Unsaved:{unsaved}  Parent:{_editor_source_session_id or '--'}",
            "Ops: q quant  [/] nudge  t/g transpose  +/- velocity  c cc-scale  k cc-thin",
            "Ops: r program  o split  O merge  y copy  v paste  u undo  U redo  Enter apply  Esc cancel",
        ]
    elif _ui_mode == _MODE_MEM_PLAYBACK:
        status_line = "Audition: Enter play/stop, left/right page, ,/. session"
    else:
        status_line = "Browser: ,/. or left/right session, PgUp/PgDn page, e=export i=import s=save"

    legend = f"{base._channel_legend()} [h=live m=browser e=edit p=audition]"
    footer_left = (
        f"Range: {base._notename(base.pitch_low)}-{base._notename(base.pitch_high)}  "
        f"T/col:{base.TICKS_PER_COL}  Active:{projection['active_count']}  Cols:{roll_cols}"
    )
    if _memory_status:
        footer_left = f"{footer_left}  {_memory_status}"

    footer_right = ""
    if session is not None and session.export_path:
        footer_right = os.path.basename(str(session.export_path))

    tick_right = int(page_start + (roll_cols - 1) * int(base.TICKS_PER_COL))
    return {
        "cols": cols,
        "rows": rows,
        "y0": y0,
        "top": top,
        "note_rows": int(projection["note_rows"]),
        "roll_cols": roll_cols,
        "header_left": header_left,
        "header_right": header_right,
        "legend": legend,
        "status_line": status_line,
        "editor_lines": editor_lines,
        "metadata_lines": metadata_lines,
        "input_mode": bool(base.vis_input_mode),
        "input_text": str(base.vis_input_text),
        "timeline": timeline,
        "pitches": list(projection["pitches"]),
        "grid": list(projection["grid"]),
        "columns": list(projection["columns"]),
        "spans": list(projection["spans"]),
        "cc_lanes": list(projection["cc_lanes"]),
        "cc_lines": list(projection["cc_lines"]),
        "tick_right": tick_right,
        "tick_now": tick_right,
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
    status_line = str(view.get("status_line", ""))
    editor_lines = [Line.plain(str(x)) for x in list(view.get("editor_lines", []))]
    metadata_lines = [Line.plain(str(x)) for x in list(view.get("metadata_lines", []))]
    footer = base._merge_left_right(str(view["footer_left"]), str(view["footer_right"]), cols)
    children = [
        TextBlock(lines=[Line.plain(header), Line.plain(info), Line.plain(status_line)] + editor_lines + metadata_lines),
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


def _set_status(msg: str, level: str = "info") -> None:
    global _memory_status, _status_level
    _memory_status = str(msg)
    _status_level = str(level)


def _set_mode(mode: str) -> None:
    global _ui_mode
    _ui_mode = mode


def _selected_session_meta() -> tuple[SessionModel | None, list[dict]]:
    sessions = _memory_list()
    sess, _live = _selected_session(running=False)
    return sess, sessions


def _editor_current_session() -> SessionModel | None:
    if _editor is None:
        return None
    try:
        return _editor.current_session
    except Exception:
        return None


def _editor_sync_status() -> None:
    global _editor_revision_id, _editor_dirty
    if _editor is None:
        _editor_revision_id = "r0"
        _editor_dirty = False
        return
    try:
        hist = list(_editor.revision_history)
        _editor_revision_id = str(hist[-1].revision_id if hist else "r0")
        _editor_dirty = len(hist) > 1
    except Exception:
        _editor_revision_id = "r0"
        _editor_dirty = False


def _editor_begin_for_selected() -> bool:
    global _editor, _editor_source_session_id
    session, _sessions = _selected_session_meta()
    if session is None:
        _set_status("Edit start failed: no session selected", "error")
        return False
    sid = str(session.header.session_id)
    if _editor is not None and sid == _editor_source_session_id:
        _editor_sync_status()
        return True
    try:
        _editor = SessionEditor(source_session=session)
        _editor_source_session_id = sid
        _editor_selection["start"] = int(session.header.start_tick)
        _editor_selection["end"] = int(max(session.header.start_tick, session.header.stop_tick))
        _editor_set_selection_from_page()
        _editor_sync_status()
        _set_status(f"Editor opened for {sid}")
        return True
    except Exception as exc:
        _editor = None
        _editor_source_session_id = ""
        _editor_sync_status()
        _set_status(f"Edit start failed: {exc}", "error")
        return False


def _editor_exit(*, apply_changes: bool) -> None:
    global _editor, _editor_source_session_id
    if _editor is None:
        _set_mode(_MODE_MEM_BROWSER)
        _set_status("Mode: MEM_BROWSER")
        return
    if not apply_changes:
        _editor = None
        _editor_source_session_id = ""
        _editor_sync_status()
        _set_mode(_MODE_MEM_BROWSER)
        _set_status("Edit canceled")
        return
    _editor_sync_status()
    _set_mode(_MODE_MEM_BROWSER)
    if _editor_dirty:
        _set_status("Edit kept in staging; press s to save")
    else:
        _set_status("No changes to apply")


def _editor_apply_op(op: dict, label: str) -> bool:
    if _editor is None:
        _set_status("Editor not active", "error")
        return False
    try:
        _editor.apply(op)
        _editor_sync_status()
        _set_status(f"Edit: {label}")
        return True
    except Exception as exc:
        _set_status(f"Edit failed: {exc}", "error")
        return False


def _editor_set_selection_from_page() -> bool:
    if _editor is None:
        return False
    lo = int(min(_editor_selection.get("start", 0), _editor_selection.get("end", 0)))
    hi = int(max(_editor_selection.get("start", 0), _editor_selection.get("end", 0)))
    try:
        _editor.apply({"type": "set_selection", "tick_start": lo, "tick_end": hi})
        _set_status(f"Selection {lo}..{hi}")
        return True
    except Exception as exc:
        _set_status(f"Selection failed: {exc}", "error")
        return False


def _navigate_session(step: int) -> None:
    global _view_session_idx, _view_session_id
    sessions = _memory_list()
    if not sessions:
        _set_status("No sessions available", "error")
        return
    ids = [str(item.get("id", "")) for item in sessions if str(item.get("id", ""))]
    if not ids:
        _set_status("No sessions available", "error")
        return
    if _view_session_id in ids:
        cur = ids.index(_view_session_id)
    elif _view_session_idx >= 0:
        cur = min(_view_session_idx, len(ids) - 1)
    else:
        cur = len(ids) - 1
    nxt = max(0, min(len(ids) - 1, cur + int(step)))
    _view_session_idx = nxt
    _view_session_id = ids[nxt]
    _set_status(f"Session {_view_session_idx + 1}/{len(ids)}")


def _navigate_page(step: int) -> None:
    global _view_page_idx
    if not _view_session_id:
        _set_status("No session selected", "error")
        return
    # Use derived cache when available to avoid a session fetch.
    if _derived_session_id == _view_session_id and _derived_page_count > 0:
        page_count = _derived_page_count
    else:
        sess = _memory_get_ref(_view_session_id)
        if sess is None:
            _set_status("Session unavailable", "error")
            return
        page_count = max(1, _session_page_count(sess, _last_roll_cols))
    _view_page_idx = max(0, min(page_count - 1, int(_view_page_idx) + int(step)))
    _set_status(f"Page {_view_page_idx + 1}/{page_count}")


def _save_session_snapshot() -> None:
    session, _sessions = _selected_session_meta()
    if _ui_mode == _MODE_MEM_EDIT and _editor is not None:
        session = _editor_current_session()
    if session is None:
        _set_status("Save failed: no session selected", "error")
        return
    try:
        export_root = _MEMORY_EXPORT_DIR if os.path.isabs(_MEMORY_EXPORT_DIR) else os.path.join(os.getcwd(), _MEMORY_EXPORT_DIR)
        os.makedirs(export_root, exist_ok=True)
        parent_sid = _editor_source_session_id or str(session.header.session_id)
        rev = _editor_revision_id if (_ui_mode == _MODE_MEM_EDIT and _editor is not None) else "r0"
        sid = f"edit-{parent_sid.split('@rev-')[0]}-{rev}-{uuid4().hex[:6]}"
        session.header.session_id = sid
        session.export_path = f"parent={parent_sid};revision={rev}"
        session_path = storage.save_session(export_root, session)
        rows = [row for row in storage.load_index(export_root) if str(row.get("id", "")) != sid]
        rows.append(
            storage.build_index_record(
                session,
                session_path=session_path,
                midi_path=session.export_path,
                origin=f"edit-parent:{parent_sid};revision:{rev}",
            )
        )
        rows.sort(key=lambda row: float(row.get("created_ts", 0.0)), reverse=True)
        storage.save_index(export_root, rows)
        _set_status(f"Saved edited session: {sid}")
    except Exception as exc:
        _set_status(f"Save failed: {exc}", "error")


def _export_session_midi() -> None:
    session, _sessions = _selected_session_meta()
    if _ui_mode == _MODE_MEM_EDIT and _editor is not None:
        session = _editor_current_session()
    if session is None:
        _set_status("Export failed: no session selected", "error")
        return
    try:
        # Use the engine's per-boot MIDI dir so manual exports land alongside auto-exports.
        eng = _engine()
        if eng is not None:
            try:
                midi_root = eng.memory_midi_dir()
            except Exception:
                midi_root = None
        else:
            midi_root = None
        if not midi_root:
            export_root = _MEMORY_EXPORT_DIR if os.path.isabs(_MEMORY_EXPORT_DIR) else os.path.join(os.getcwd(), _MEMORY_EXPORT_DIR)
            midi_root = export_root
        os.makedirs(midi_root, exist_ok=True)
        out_path = os.path.join(midi_root, f"manual-export-{session.header.session_id}.mid")
        mid = midi_io.export_session_midi(session, out_path)
        if not mid:
            raise RuntimeError("no MIDI events to export")
        _set_status(f"Exported MIDI: {os.path.basename(mid)}")
    except Exception as exc:
        _set_status(f"Export failed: {exc}", "error")


def _import_library_session() -> None:
    lib_dir = _MEMORY_LIBRARY_DIR.strip()
    if not lib_dir:
        _set_status("Import failed: memory_library_dir is empty", "error")
        return
    abs_lib = lib_dir if os.path.isabs(lib_dir) else os.path.join(os.getcwd(), lib_dir)
    try:
        mids = sorted([name for name in os.listdir(abs_lib) if name.lower().endswith('.mid')])
    except Exception as exc:
        _set_status(f"Import failed: {exc}", "error")
        return
    if not mids:
        _set_status("Import failed: no .mid files in library", "error")
        return
    chosen = os.path.join(abs_lib, mids[-1])
    try:
        sid = f"import-{os.path.splitext(os.path.basename(chosen))[0]}-{uuid4().hex[:6]}"
        session = midi_io.import_midi_file(chosen, session_id=sid)
        if session is None:
            raise RuntimeError("unsupported midi file")
        export_root = _MEMORY_EXPORT_DIR if os.path.isabs(_MEMORY_EXPORT_DIR) else os.path.join(os.getcwd(), _MEMORY_EXPORT_DIR)
        session.export_path = chosen
        storage.save_session(export_root, session)
        _set_status(f"Imported: {os.path.basename(chosen)}")
    except Exception as exc:
        _set_status(f"Import failed: {exc}", "error")


def on_tick(state):
    global _last_running, _last_tick
    global _view_session_id, _view_session_idx, _session_cache_id, _session_cache_t
    global _sessions_meta_cache_t, _derived_cache_t, _derived_session_id
    global _session_cache

    base.on_tick(state)

    running = bool(state.get("running", False))
    tick_now = int(state.get("tick", _last_tick))
    _last_tick = tick_now

    # Detect transport stop: reset session selection so we show the newly
    # finalized session instead of whatever was browsed before recording.
    if _last_running and not running and _ui_mode != _MODE_LIVE:
        # Grab a fresh snapshot right now to capture notes released in the
        # last SESSION_CACHE_TTL window that may not yet be in _session_cache.
        # Only overwrites if _current is still live (before _finalize_session runs).
        _snap = _memory_get_current_display()
        if _snap is not None:
            _session_cache = _snap
        _view_session_id = ""
        _view_session_idx = -1
        _session_cache_id = ""
        _session_cache_t = 0.0
        # Force immediate metadata refresh so the just-finalized recording
        # is selectable on the first post-stop frame.
        _sessions_meta_cache_t = 0.0
        _derived_cache_t = 0.0
        # Reset derived ID so _refresh_derived() re-runs on whatever session
        # is shown (snapshot or finalized), picking up any spans added since
        # the last live derive and ensuring _derived_span_count is current.
        _derived_session_id = ""

    # Rate-limit memory_status lock acquisition to 5 Hz.
    global _status_poll_t
    now_t = time.time()
    if now_t - _status_poll_t >= _STATUS_POLL_TTL:
        _status_poll_t = now_t
        status = {}
        eng = _engine()
        if eng is not None:
            try:
                status = eng.memory_status()
            except Exception:
                status = {}
        if status.get("armed") and status.get("current_id"):
            _set_status("REC")
        elif _ui_mode != _MODE_LIVE and not _memory_status:
            _set_status("MEM")

    _last_running = running


def keypress(ch):
    global _ui_mode, _editor_tool

    s = str(ch)
    if s.lower() == _MEMORY_TOGGLE_KEY:
        eng = _engine()
        if _ui_mode == _MODE_LIVE:
            _set_mode(_MODE_MEM_BROWSER)
            if eng is not None:
                try:
                    eng.memory_start()
                except Exception:
                    pass
            _set_status("Mode: MEM_BROWSER (armed)")
        else:
            _set_mode(_MODE_LIVE)
            if eng is not None:
                try:
                    eng.memory_stop()
                except Exception:
                    pass
            _set_status("Mode: LIVE")
        return True

    if s.lower() == "m":
        _set_mode(_MODE_MEM_BROWSER)
        _set_status("Mode: MEM_BROWSER")
        return True
    if s.lower() == "e":
        if _editor_begin_for_selected():
            _set_mode(_MODE_MEM_EDIT)
            _set_status("Mode: MEM_EDIT")
        return True
    if s.lower() == "p":
        _set_mode(_MODE_MEM_PLAYBACK)
        _set_status("Mode: MEM_PLAYBACK")
        return True

    if base.vis_input_mode:
        return bool(base.keypress(ch))

    if _ui_mode == _MODE_LIVE:
        return bool(base.keypress(ch))

    if s == ",":
        _navigate_session(-1)
        return True
    if s == ".":
        _navigate_session(1)
        return True

    if ch.is_sequence and ch.name in {"KEY_LEFT", "KEY_PPAGE"}:
        _navigate_page(-1)
        return True
    if ch.is_sequence and ch.name in {"KEY_RIGHT", "KEY_NPAGE"}:
        _navigate_page(1)
        return True

    if _ui_mode == _MODE_MEM_EDIT:
        if ch.is_sequence and ch.name == "KEY_ESCAPE":
            _editor_exit(apply_changes=False)
            return True
        if s in {"\n", "\r"} or (ch.is_sequence and ch.name == "KEY_ENTER"):
            _editor_exit(apply_changes=True)
            return True

        if ch.is_sequence and ch.name == "KEY_UP":
            _editor_selection["start"] = max(0, int(_editor_selection.get("start", 0)) - int(base.TICKS_PER_COL))
            _editor_selection["end"] = max(_editor_selection["start"], int(_editor_selection.get("end", 0)) - int(base.TICKS_PER_COL))
            _editor_set_selection_from_page()
            return True
        if ch.is_sequence and ch.name == "KEY_DOWN":
            _editor_selection["start"] = int(_editor_selection.get("start", 0)) + int(base.TICKS_PER_COL)
            _editor_selection["end"] = int(_editor_selection.get("end", 0)) + int(base.TICKS_PER_COL)
            _editor_set_selection_from_page()
            return True

        ops = {
            "q": ({"type": "quantize", "grid": int(base.TICKS_PER_COL)}, "quantize"),
            "[": ({"type": "nudge", "delta_ticks": -int(base.TICKS_PER_COL)}, "nudge -"),
            "]": ({"type": "nudge", "delta_ticks": int(base.TICKS_PER_COL)}, "nudge +"),
            "t": ({"type": "transpose", "semitones": 1}, "transpose +1"),
            "g": ({"type": "transpose", "semitones": -1}, "transpose -1"),
            "+": ({"type": "velocity", "scale": 1.0, "offset": 8}, "velocity +8"),
            "-": ({"type": "velocity", "scale": 1.0, "offset": -8}, "velocity -8"),
            "c": ({"type": "cc_scale", "scale": 1.0, "offset": 8}, "cc scale +8"),
            "k": ({"type": "cc_thin", "step": 2}, "cc thin/2"),
            "r": ({"type": "program_change_set", "channel": 1, "tick": int(min(_editor_selection.get("start", 0), _editor_selection.get("end", 0))), "program": 0, "replace": True}, "program set ch1=0"),
            "o": ({"type": "split_clip", "tick": int(min(_editor_selection.get("start", 0), _editor_selection.get("end", 0)))}, "split clip"),
            "O": ({"type": "merge_clips", "first_index": 0, "second_index": 1}, "merge clips"),
            "y": ({"type": "copy_region", "tick_start": int(min(_editor_selection.get("start", 0), _editor_selection.get("end", 0))), "tick_end": int(max(_editor_selection.get("start", 0), _editor_selection.get("end", 0)))}, "copy"),
            "v": ({"type": "paste_region", "dest_tick": int(max(_editor_selection.get("start", 0), _editor_selection.get("end", 0)))}, "paste"),
        }
        if s in ops:
            _editor_tool = s.upper()
            op, label = ops[s]
            _editor_apply_op(op, label)
            return True

        if s == "u":
            if _editor is None or _editor.undo() is None:
                _set_status("Undo unavailable", "error")
            else:
                _editor_sync_status()
                _set_status("Undo")
            return True
        if s == "U":
            if _editor is None or _editor.redo() is None:
                _set_status("Redo unavailable", "error")
            else:
                _editor_sync_status()
                _set_status("Redo")
            return True

    if _ui_mode == _MODE_MEM_PLAYBACK and (s in {"\n", "\r"} or (ch.is_sequence and ch.name == "KEY_ENTER")):
        _set_status("Playback audition toggled")
        return True

    if s.lower() == "s":
        _save_session_snapshot()
        return True
    if s.lower() == "i":
        _import_library_session()
        return True
    if s.lower() == "x":
        _export_session_midi()
        return True

    return bool(base.keypress(ch))


def compositor_cache_key() -> str | None:
    """Return an opaque key for the current render state.

    midicrt's draw loop calls this (if present) to decide whether the
    compositor page buffer can be reused instead of re-rendered.

    Returns None to disable caching (e.g., during live recording where
    the display changes every tick).
    """
    if _ui_mode == _MODE_LIVE:
        return None   # live view changes every tick
    # Non-live page-16 modes still have time-based compositor effects
    # (overlap flashing, row fade), so static cache keys would freeze them.
    return None


def build_widget(state):
    use_sparse = state.get("render_backend") == "compositor"
    if _ui_mode == _MODE_LIVE:
        view = _build_live_view(state, build_grid=not use_sparse)
    else:
        view = _build_memory_view(state, build_grid=not use_sparse)
    return _view_to_widget(view)


def draw(state):
    if _ui_mode == _MODE_LIVE:
        return base.draw(state)

    view = _build_memory_view(state, build_grid=True)
    cols = int(view["cols"])
    rows = int(view["rows"])
    y0 = int(view["y0"])

    draw_line(y0, base._merge_left_right(view["header_left"], view["header_right"], cols))
    base._draw_right_reverse(y0, view["header_right"], cols)
    draw_line(y0 + 1, str(view["legend"])[:cols])
    draw_line(y0 + 2, str(view.get("status_line", ""))[:cols])
    editor_lines = list(view.get("editor_lines", []))[:3]
    for i, eline in enumerate(editor_lines):
        draw_line(y0 + 3 + i, str(eline)[:cols])
    meta_y = y0 + 3 + len(editor_lines)
    for i, mline in enumerate(list(view.get("metadata_lines", []))[:6]):
        draw_line(meta_y + i, str(mline)[:cols])

    top = int(view["top"])
    draw_line(top, (f"{'Bars':>7} |" + str(view["timeline"]))[:cols])

    for row, pitch in enumerate(view["pitches"]):
        label = f"{base._notename(int(pitch)):>7} |"
        chars = []
        for cell in view["grid"][row]:
            v = int(cell.velocity)
            if v >= 96:
                chars.append("#")
            elif v >= 48:
                chars.append("+")
            elif v > 0:
                chars.append("*")
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
