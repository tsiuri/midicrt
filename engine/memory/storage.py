from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from typing import Any

from engine.memory.session_model import (
    MidiEvent,
    NoteSpan,
    SessionHeader,
    SessionModel,
    TempoSegment,
    TimeSignatureSegment,
)

INDEX_FILE = "session_index.json"
SESSIONS_DIR = "sessions"


def atomic_write_text(path: str, payload: str) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}-{time.time_ns()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def atomic_write_json(path: str, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _session_to_dict(session: SessionModel) -> dict[str, Any]:
    out = asdict(session)
    active_notes = out.get("active_notes", {}) if isinstance(out, dict) else {}
    if isinstance(active_notes, dict):
        out["active_notes"] = {f"{int(k[0])}:{int(k[1])}": [int(v[0]), int(v[1])] for k, v in active_notes.items()}
    return out


def _session_from_dict(data: dict[str, Any]) -> SessionModel:
    header_in = data.get("header") if isinstance(data.get("header"), dict) else {}
    header = SessionHeader(
        session_id=str(header_in.get("session_id", "")),
        start_tick=int(header_in.get("start_tick", 0)),
        stop_tick=int(header_in.get("stop_tick", header_in.get("start_tick", 0))),
        ppqn=int(header_in.get("ppqn", 24)),
        bpm=float(header_in.get("bpm", 120.0) or 120.0),
        tempo_segments=[TempoSegment(**seg) for seg in header_in.get("tempo_segments", []) if isinstance(seg, dict)],
        time_signature_segments=[
            TimeSignatureSegment(**seg) for seg in header_in.get("time_signature_segments", []) if isinstance(seg, dict)
        ],
    )
    model = SessionModel(
        schema_name=str(data.get("schema_name", "midicrt.session")),
        schema_version=str(data.get("schema_version", "1.0.0")),
        header=header,
        events=[MidiEvent(**ev) for ev in data.get("events", []) if isinstance(ev, dict)],
        note_spans=[NoteSpan(**span) for span in data.get("note_spans", []) if isinstance(span, dict)],
        active_notes={
            tuple(map(int, key.split(":"))): (int(value[0]), int(value[1]))
            for key, value in (data.get("active_notes", {}) or {}).items()
            if isinstance(key, str) and isinstance(value, (list, tuple)) and len(value) >= 2 and ":" in key
        },
        recent_hits=[tuple(hit) for hit in data.get("recent_hits", []) if isinstance(hit, (list, tuple)) and len(hit) >= 4],
        cc_events=[tuple(ev) for ev in data.get("cc_events", []) if isinstance(ev, (list, tuple)) and len(ev) >= 5],
        cc_order=[tuple(ev) for ev in data.get("cc_order", []) if isinstance(ev, (list, tuple)) and len(ev) >= 2],
        export_path=str(data.get("export_path")) if data.get("export_path") else None,
        start_time=float(data.get("start_time", 0.0) or 0.0),
        stop_time=float(data.get("stop_time", 0.0) or 0.0),
        _seq=int(data.get("_seq", len(data.get("events", [])) or 0)),
    )
    return model


def session_counts(session: SessionModel) -> tuple[dict[str, int], list[int]]:
    counts: dict[str, int] = {}
    channel_usage: set[int] = set()
    for ev in session.events:
        counts[ev.kind] = counts.get(ev.kind, 0) + 1
        if ev.channel is not None:
            channel_usage.add(int(ev.channel))
    return counts, sorted(channel_usage)


def build_index_record(session: SessionModel, *, session_path: str, midi_path: str | None = None, origin: str = "capture") -> dict[str, Any]:
    start_tick = int(session.header.start_tick)
    stop_tick = max(int(session.header.stop_tick), start_tick)
    duration_ticks = max(0, stop_tick - start_tick)
    bpm = float(session.header.bpm or 120.0)
    duration_seconds = (duration_ticks / max(1, int(session.header.ppqn))) * (60.0 / max(1e-6, bpm))
    event_counts, channels = session_counts(session)
    created_ts = float(session.stop_time or session.start_time or time.time())
    return {
        "id": str(session.header.session_id),
        "created_ts": created_ts,
        "start_tick": start_tick,
        "stop_tick": stop_tick,
        "duration_ticks": duration_ticks,
        "duration_seconds": float(duration_seconds),
        "event_count": int(len(session.events)),
        "note_span_count": int(len(session.note_spans)),
        "event_counts": event_counts,
        "channel_usage": channels,
        "session_path": str(session_path),
        "midi_path": str(midi_path or session.export_path or ""),
        "origin": str(origin),
    }


def _index_path(root_dir: str) -> str:
    return os.path.join(root_dir, INDEX_FILE)


def _sessions_dir(root_dir: str) -> str:
    return os.path.join(root_dir, SESSIONS_DIR)


def load_index(root_dir: str) -> list[dict[str, Any]]:
    path = _index_path(root_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for row in data:
        if isinstance(row, dict) and row.get("id"):
            out.append(row)
    return out


def save_index(root_dir: str, rows: list[dict[str, Any]]) -> None:
    atomic_write_json(_index_path(root_dir), rows)


def save_session(root_dir: str, session: SessionModel) -> str:
    out_dir = _sessions_dir(root_dir)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{session.header.session_id}.json")
    atomic_write_json(path, _session_to_dict(session))
    return path


def load_session(path: str) -> SessionModel | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return _session_from_dict(data)
    except Exception:
        return None
