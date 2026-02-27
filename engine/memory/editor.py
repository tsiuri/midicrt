from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from engine.memory.session_model import MidiEvent, NoteSpan, SessionModel


@dataclass
class TimeSelection:
    tick_start: int
    tick_end: int
    channels: set[int] | None = None

    def normalize(self) -> "TimeSelection":
        start = int(self.tick_start)
        end = int(self.tick_end)
        if end < start:
            start, end = end, start
        channels = None if self.channels is None else {int(ch) for ch in self.channels}
        return TimeSelection(tick_start=start, tick_end=end, channels=channels)

    def contains(self, event: MidiEvent) -> bool:
        tick = int(event.tick)
        if tick < int(self.tick_start) or tick > int(self.tick_end):
            return False
        if self.channels is None:
            return True
        if event.channel is None:
            return False
        return int(event.channel) in self.channels


@dataclass
class SessionRevision:
    revision_id: str
    parent_revision_id: str | None
    created_ts: float
    session: SessionModel
    op: dict[str, Any] | None = None
    clips: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class SessionClipboard:
    tick_start: int
    tick_end: int
    events: list[MidiEvent]


class SessionEditor:
    """Applies non-destructive edits to session revisions with undo/redo support."""

    def __init__(self, source_session: SessionModel):
        self._source_session = copy.deepcopy(source_session)
        self._selection: TimeSelection | None = None
        self._clipboard: SessionClipboard | None = None
        self._redo_stack: list[SessionRevision] = []
        self._op_log: list[dict[str, Any]] = []

        base = self._clone_session(self._source_session)
        self._regenerate_derived(base)
        self._history: list[SessionRevision] = [
            SessionRevision(
                revision_id="r0",
                parent_revision_id=None,
                created_ts=time.time(),
                session=base,
                op=None,
                clips=[(int(base.header.start_tick), int(base.header.stop_tick))],
            )
        ]

    @property
    def source_session(self) -> SessionModel:
        return self._clone_session(self._source_session)

    @property
    def current_session(self) -> SessionModel:
        return self._clone_session(self._history[-1].session)

    @property
    def selection(self) -> TimeSelection | None:
        return None if self._selection is None else self._selection.normalize()

    @property
    def op_log(self) -> list[dict[str, Any]]:
        return list(self._op_log)

    @property
    def revision_history(self) -> list[SessionRevision]:
        return list(self._history)

    def apply(self, op: dict[str, Any]) -> SessionModel:
        op_type = str(op.get("type", "")).strip()
        if not op_type:
            raise ValueError("operation type is required")

        if op_type == "set_selection":
            self._selection = TimeSelection(
                tick_start=int(op.get("tick_start", 0)),
                tick_end=int(op.get("tick_end", 0)),
                channels={int(ch) for ch in op.get("channels", [])} if op.get("channels") is not None else None,
            ).normalize()
            return self.current_session

        session = self._clone_session(self._history[-1].session)
        clips = list(self._history[-1].clips)

        handler_name = f"_op_{op_type}"
        if not hasattr(self, handler_name):
            raise ValueError(f"unsupported operation type: {op_type}")
        getattr(self, handler_name)(session, op, clips)

        self._regenerate_derived(session)
        parent_id = self._history[-1].revision_id
        new_rev_id = f"r{len(self._history)}"
        session.header.session_id = self._revision_session_id(session.header.session_id, new_rev_id)
        revision = SessionRevision(
            revision_id=new_rev_id,
            parent_revision_id=parent_id,
            created_ts=time.time(),
            session=session,
            op=copy.deepcopy(op),
            clips=clips,
        )
        self._history.append(revision)
        self._redo_stack.clear()
        self._op_log.append(copy.deepcopy(op))
        return self.current_session

    def undo(self) -> SessionModel | None:
        if len(self._history) <= 1:
            return None
        self._redo_stack.append(self._history.pop())
        return self.current_session

    def redo(self) -> SessionModel | None:
        if not self._redo_stack:
            return None
        self._history.append(self._redo_stack.pop())
        return self.current_session

    def _selected(self, event: MidiEvent) -> bool:
        if self._selection is None:
            return True
        return self._selection.contains(event)

    def _op_quantize(self, session: SessionModel, op: dict[str, Any], _clips: list[tuple[int, int]]) -> None:
        grid = int(op.get("grid", 0) or 0)
        if grid <= 0:
            return
        for ev in session.events:
            if not self._selected(ev):
                continue
            ev.tick = int(round(int(ev.tick) / grid) * grid)

    def _op_nudge(self, session: SessionModel, op: dict[str, Any], _clips: list[tuple[int, int]]) -> None:
        delta = int(op.get("delta_ticks", 0) or 0)
        if delta == 0:
            return
        for ev in session.events:
            if self._selected(ev):
                ev.tick = max(0, int(ev.tick) + delta)

    def _op_transpose(self, session: SessionModel, op: dict[str, Any], _clips: list[tuple[int, int]]) -> None:
        semitones = int(op.get("semitones", 0) or 0)
        if semitones == 0:
            return
        for ev in session.events:
            if not self._selected(ev):
                continue
            if ev.kind in {"note_on", "note_off", "poly_aftertouch"} and ev.note is not None:
                ev.note = min(127, max(0, int(ev.note) + semitones))

    def _op_velocity(self, session: SessionModel, op: dict[str, Any], _clips: list[tuple[int, int]]) -> None:
        scale = float(op.get("scale", 1.0) or 1.0)
        offset = int(op.get("offset", 0) or 0)
        for ev in session.events:
            if not self._selected(ev):
                continue
            if ev.kind in {"note_on", "note_off"} and ev.velocity is not None:
                value = int(round(int(ev.velocity) * scale)) + offset
                ev.velocity = min(127, max(0, value))

    def _op_cc_delete_range(self, session: SessionModel, op: dict[str, Any], _clips: list[tuple[int, int]]) -> None:
        controls = op.get("controls")
        control_set = {int(c) for c in controls} if controls else None
        session.events = [
            ev
            for ev in session.events
            if not (
                ev.kind == "control_change"
                and self._selected(ev)
                and (control_set is None or int(ev.control or -1) in control_set)
            )
        ]

    def _op_cc_scale(self, session: SessionModel, op: dict[str, Any], _clips: list[tuple[int, int]]) -> None:
        scale = float(op.get("scale", 1.0) or 1.0)
        offset = int(op.get("offset", 0) or 0)
        controls = op.get("controls")
        control_set = {int(c) for c in controls} if controls else None
        for ev in session.events:
            if ev.kind != "control_change" or not self._selected(ev):
                continue
            if control_set is not None and int(ev.control or -1) not in control_set:
                continue
            value = int(round(int(ev.value or 0) * scale)) + offset
            ev.value = min(127, max(0, value))

    def _op_cc_thin(self, session: SessionModel, op: dict[str, Any], _clips: list[tuple[int, int]]) -> None:
        step = max(1, int(op.get("step", 2) or 2))
        controls = op.get("controls")
        control_set = {int(c) for c in controls} if controls else None
        counters: dict[tuple[int, int], int] = {}
        kept: list[MidiEvent] = []
        for ev in session.events:
            if ev.kind != "control_change" or not self._selected(ev):
                kept.append(ev)
                continue
            if control_set is not None and int(ev.control or -1) not in control_set:
                kept.append(ev)
                continue
            key = (int(ev.channel or 0), int(ev.control or -1))
            idx = counters.get(key, 0)
            counters[key] = idx + 1
            if idx % step == 0:
                kept.append(ev)
        session.events = kept

    def _op_program_change_set(self, session: SessionModel, op: dict[str, Any], _clips: list[tuple[int, int]]) -> None:
        channel = int(op["channel"])
        tick = int(op.get("tick", session.header.start_tick))
        program = min(127, max(0, int(op.get("program", 0))))
        replace = bool(op.get("replace", True))
        if replace:
            session.events = [
                ev
                for ev in session.events
                if not (ev.kind == "program_change" and int(ev.channel or 0) == channel and int(ev.tick) == tick)
            ]
        max_seq = max([int(ev.seq) for ev in session.events] + [0])
        session.events.append(
            MidiEvent(
                kind="program_change",
                tick=tick,
                seq=max_seq + 1,
                channel=channel,
                program=program,
                source="synth",
            )
        )

    def _op_split_clip(self, _session: SessionModel, op: dict[str, Any], clips: list[tuple[int, int]]) -> None:
        split_tick = int(op["tick"])
        for idx, (start, end) in enumerate(list(clips)):
            if start < split_tick < end:
                clips.pop(idx)
                clips.insert(idx, (start, split_tick))
                clips.insert(idx + 1, (split_tick, end))
                break

    def _op_merge_clips(self, _session: SessionModel, op: dict[str, Any], clips: list[tuple[int, int]]) -> None:
        first = int(op.get("first_index", 0))
        second = int(op.get("second_index", first + 1))
        if first < 0 or second >= len(clips) or first >= second:
            return
        a_start, a_end = clips[first]
        b_start, b_end = clips[second]
        if a_end != b_start:
            return
        clips[first] = (a_start, b_end)
        clips.pop(second)

    def _op_copy_region(self, session: SessionModel, op: dict[str, Any], _clips: list[tuple[int, int]]) -> None:
        tick_start = int(op["tick_start"])
        tick_end = int(op["tick_end"])
        if tick_end < tick_start:
            tick_start, tick_end = tick_end, tick_start
        channels = op.get("channels")
        channel_set = {int(ch) for ch in channels} if channels else None
        events = []
        for ev in session.events:
            if int(ev.tick) < tick_start or int(ev.tick) > tick_end:
                continue
            if channel_set is not None and int(ev.channel or -1) not in channel_set:
                continue
            clone = copy.deepcopy(ev)
            clone.tick = int(ev.tick) - tick_start
            events.append(clone)
        self._clipboard = SessionClipboard(tick_start=tick_start, tick_end=tick_end, events=events)

    def _op_paste_region(self, session: SessionModel, op: dict[str, Any], _clips: list[tuple[int, int]]) -> None:
        if self._clipboard is None:
            return
        dest_tick = int(op["dest_tick"])
        channel_override = op.get("channel")
        max_seq = max([int(ev.seq) for ev in session.events] + [0])
        inserted: list[MidiEvent] = []
        for idx, ev in enumerate(self._clipboard.events, start=1):
            clone = copy.deepcopy(ev)
            clone.tick = max(0, dest_tick + int(ev.tick))
            clone.seq = max_seq + idx
            if channel_override is not None:
                clone.channel = int(channel_override)
            inserted.append(clone)
        session.events.extend(inserted)

    def _clone_session(self, session: SessionModel) -> SessionModel:
        return copy.deepcopy(session)

    def _revision_session_id(self, base_id: str, revision_id: str) -> str:
        stem = str(base_id or "session")
        stem = stem.split("@rev-")[0]
        return f"{stem}@rev-{revision_id}-{uuid4().hex[:8]}"

    def _regenerate_derived(self, session: SessionModel) -> None:
        session.events.sort(key=lambda ev: (int(ev.tick), int(ev.seq), str(ev.kind), int(ev.channel or 0)))
        for idx, ev in enumerate(session.events, start=1):
            ev.seq = idx

        session.note_spans = []
        session.active_notes = {}
        session.cc_events = []
        session.cc_order = []

        cc_seen: set[tuple[int, int]] = set()

        for ev in session.events:
            tick = int(ev.tick)
            if ev.kind == "note_on":
                ch = int(ev.channel or 0)
                note = int(ev.note or 0)
                vel = int(ev.velocity or 0)
                if vel > 0:
                    prev = session.active_notes.pop((ch, note), None)
                    if prev is not None:
                        session.note_spans.append(
                            NoteSpan(start_tick=int(prev[0]), end_tick=tick, pitch=note, channel=ch, velocity=int(prev[1]))
                        )
                    session.active_notes[(ch, note)] = (tick, vel)
                else:
                    self._close_note(session, ch, note, tick)
            elif ev.kind == "note_off":
                self._close_note(session, int(ev.channel or 0), int(ev.note or 0), tick)
            elif ev.kind == "control_change":
                ch = int(ev.channel or 0)
                control = int(ev.control or 0)
                value = int(ev.value or 0)
                session.cc_events.append((tick, ch, control, value, 0.0))
                key = (ch, control)
                if key not in cc_seen:
                    cc_seen.add(key)
                    session.cc_order.append(key)
                if control == 123:
                    self._close_channel_notes(session, ch, tick)

        stop_tick = max([int(session.header.start_tick)] + [int(ev.tick) for ev in session.events])
        for (ch, note), (start, vel) in list(session.active_notes.items()):
            session.note_spans.append(
                NoteSpan(start_tick=int(start), end_tick=stop_tick, pitch=int(note), channel=int(ch), velocity=int(vel))
            )
            session.active_notes.pop((ch, note), None)

        session.header.stop_tick = stop_tick

    def _close_note(self, session: SessionModel, channel: int, note: int, end_tick: int) -> None:
        active = session.active_notes.pop((int(channel), int(note)), None)
        if active is None:
            return
        start_tick, velocity = active
        session.note_spans.append(
            NoteSpan(
                start_tick=int(start_tick),
                end_tick=max(int(start_tick), int(end_tick)),
                pitch=int(note),
                channel=int(channel),
                velocity=int(velocity),
            )
        )

    def _close_channel_notes(self, session: SessionModel, channel: int, end_tick: int) -> None:
        for ch, note in list(session.active_notes.keys()):
            if int(ch) == int(channel):
                self._close_note(session, int(ch), int(note), int(end_tick))
