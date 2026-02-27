from __future__ import annotations

import os
import time
from collections import deque
from copy import deepcopy
from uuid import uuid4
from typing import Any

import mido

from engine.memory import session_model
from engine.memory.session_model import SessionModel, build_session_model


class MemoryCaptureManager:
    """Engine-scoped MIDI memory capture with transport-aware session lifecycle."""

    _EVENT_KINDS = {"note_on", "note_off", "control_change", "program_change", "pitchwheel", "aftertouch", "polytouch"}

    def __init__(self, *, max_sessions: int = 32, export_dir: str = "captures/pianoroll_exp", project_root: str | None = None) -> None:
        self._max_sessions = max(1, int(max_sessions))
        self._export_dir = str(export_dir)
        self._project_root = str(project_root or os.getcwd())
        self._armed = False
        self._current: SessionModel | None = None
        self._sessions: deque[SessionModel] = deque(maxlen=self._max_sessions)
        self._export_seq = 0

    def configure(self, *, max_sessions: int | None = None, export_dir: str | None = None) -> None:
        if max_sessions is not None:
            self._max_sessions = max(1, int(max_sessions))
            self._sessions = deque(list(self._sessions)[-self._max_sessions :], maxlen=self._max_sessions)
        if export_dir is not None:
            self._export_dir = str(export_dir)

    def on_transport(self, *, tick: int, bpm: float, running: bool, prev_running: bool) -> None:
        tick = int(tick)
        bpm = float(bpm if bpm and bpm > 0 else 120.0)
        if self._armed:
            if running and (not prev_running):
                self._begin_session(start_tick=tick, bpm=bpm)
            elif (not running) and prev_running:
                self._finalize_session(stop_tick=tick)
            elif running and self._current is None:
                self._begin_session(start_tick=tick, bpm=bpm)
        if running and self._current is not None:
            self._current.header.stop_tick = tick

    def on_event(self, *, event: dict[str, Any], msg: mido.Message, tick: int) -> None:
        session = self._current
        if session is None:
            return
        kind = str(event.get("kind", ""))
        if kind not in self._EVENT_KINDS:
            return

        abs_tick = int(tick)
        session.append_event_from_message(abs_tick, msg)

        if kind == "note_on":
            ch = int(getattr(msg, "channel", 0)) + 1
            note = int(getattr(msg, "note", -1))
            vel = int(getattr(msg, "velocity", 0))
            if vel > 0:
                session.close_active_note(channel=ch, note=note, end_tick=abs_tick, emit_synth_off=True)
                session.active_notes[(ch, note)] = (abs_tick, vel)
                session.recent_hits.append((note, ch, vel, time.time()))
                if len(session.recent_hits) > 256:
                    session.recent_hits = session.recent_hits[-256:]
            else:
                session.close_active_note(channel=ch, note=note, end_tick=abs_tick, emit_synth_off=False)
            return

        if kind == "note_off":
            ch = int(getattr(msg, "channel", 0)) + 1
            note = int(getattr(msg, "note", -1))
            session.close_active_note(channel=ch, note=note, end_tick=abs_tick, emit_synth_off=False)
            return

        if kind == "control_change" and int(getattr(msg, "control", -1)) == 123:
            ch = int(getattr(msg, "channel", 0)) + 1
            session.close_channel_active_notes(channel=ch, end_tick=abs_tick, emit_synth_off=True)
            return

        if kind == "control_change":
            ch = int(getattr(msg, "channel", 0)) + 1
            cc = int(getattr(msg, "control", -1))
            value = int(getattr(msg, "value", 0))
            session.cc_events.append((abs_tick, ch, cc, value, time.time()))
            if len(session.cc_events) > 4096:
                session.cc_events = session.cc_events[-4096:]
            key = (int(ch), int(cc))
            if key not in session.cc_order:
                session.cc_order.insert(0, key)
                if len(session.cc_order) > 256:
                    del session.cc_order[256:]

    def memory_start(self, *, tick: int, bpm: float, running: bool) -> bool:
        self._armed = True
        if running and self._current is None:
            self._begin_session(start_tick=int(tick), bpm=float(bpm if bpm > 0 else 120.0))
        return True

    def memory_stop(self, *, tick: int) -> bool:
        self._armed = False
        self._finalize_session(stop_tick=int(tick))
        return False

    def memory_toggle(self, *, tick: int, bpm: float, running: bool) -> bool:
        if self._armed:
            return self.memory_stop(tick=tick)
        return self.memory_start(tick=tick, bpm=bpm, running=running)

    def memory_list(self) -> list[dict[str, Any]]:
        return [self._session_meta(sess) for sess in list(self._sessions)]

    def memory_get(self, session_id: str) -> SessionModel | None:
        if session_id == "current":
            return deepcopy(self._current) if self._current is not None else None
        for sess in self._sessions:
            if sess.header.session_id == session_id:
                return deepcopy(sess)
        return None

    def memory_delete(self, session_id: str) -> bool:
        if not session_id:
            return False
        kept = [sess for sess in self._sessions if sess.header.session_id != session_id]
        if len(kept) == len(self._sessions):
            return False
        self._sessions = deque(kept[-self._max_sessions :], maxlen=self._max_sessions)
        return True

    def status(self) -> dict[str, Any]:
        return {
            "armed": bool(self._armed),
            "current_id": self._current.header.session_id if self._current else "",
            "current_start_tick": int(self._current.header.start_tick) if self._current else 0,
            "sessions": len(self._sessions),
            "max_sessions": int(self._max_sessions),
        }

    def _begin_session(self, *, start_tick: int, bpm: float) -> None:
        if self._current is not None:
            self._finalize_session(stop_tick=int(start_tick))
        session = build_session_model(
            session_id=f"engine-memory-{uuid4().hex[:12]}",
            start_tick=int(start_tick),
            bpm=float(bpm if bpm > 0 else 120.0),
            ppqn=24,
        )
        session.start_time = time.time()
        self._current = session

    def _finalize_session(self, *, stop_tick: int) -> None:
        if self._current is None:
            return
        session = self._current
        end_tick = max(int(stop_tick), int(session.header.start_tick))
        session.flush_active_notes(end_tick=end_tick, emit_synth_off=True)
        session.header.stop_tick = end_tick
        session.stop_time = time.time()
        session.export_path = self._export_session_midi(session)
        self._sessions.append(session)
        self._current = None

    def _session_meta(self, session: SessionModel) -> dict[str, Any]:
        return {
            "id": str(session.header.session_id),
            "start_tick": int(session.header.start_tick),
            "stop_tick": int(session.header.stop_tick),
            "events": int(len(session.events)),
            "notes": int(len(session.note_spans)),
            "export_path": str(session.export_path or ""),
            "start_time": float(session.start_time or 0.0),
            "stop_time": float(session.stop_time or 0.0),
        }

    def _abs_export_dir(self) -> str:
        if os.path.isabs(self._export_dir):
            return self._export_dir
        return os.path.join(self._project_root, self._export_dir)

    def _export_session_midi(self, session: SessionModel) -> str | None:
        events = list(session.events or [])
        if not events:
            return None
        midi = mido.MidiFile(ticks_per_beat=480)
        track = mido.MidiTrack()
        midi.tracks.append(track)
        bpm = float(session.header.bpm or 120.0)
        track.append(mido.MetaMessage("set_tempo", tempo=int(mido.bpm2tempo(bpm)), time=0))
        track.append(mido.MetaMessage("track_name", name="midicrt engine memory", time=0))

        ticks_per_clock = midi.ticks_per_beat / 24.0
        start_tick = int(session.header.start_tick)
        indexed = sorted(events, key=lambda ev: (int(ev.tick), int(ev.seq)))
        prev_tick = 0
        for event in indexed:
            rel = max(0, int((int(event.tick) - start_tick) * ticks_per_clock))
            delta = max(0, rel - prev_tick)
            prev_tick = rel
            try:
                msg = session_model.to_mido_message(event)
            except Exception:
                msg = None
            if msg is not None:
                track.append(msg.copy(time=delta))

        out_dir = self._abs_export_dir()
        os.makedirs(out_dir, exist_ok=True)
        self._export_seq += 1
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out_path = os.path.join(out_dir, f"engine-memory-{stamp}-{self._export_seq:04d}.mid")
        try:
            midi.save(out_path)
            return out_path
        except Exception:
            return None
