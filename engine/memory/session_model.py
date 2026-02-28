from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import mido

SCHEMA_NAME = "midicrt.session"
SCHEMA_VERSION = "1.0.0"

EventKind = Literal[
    "note_on",
    "note_off",
    "control_change",
    "program_change",
    "pitch_bend",
    "channel_pressure",
    "poly_aftertouch",
]


@dataclass
class TempoSegment:
    start_tick: int
    bpm: float


@dataclass
class TimeSignatureSegment:
    start_tick: int
    numerator: int
    denominator: int


@dataclass
class SessionHeader:
    session_id: str
    start_tick: int
    stop_tick: int
    ppqn: int
    bpm: float
    tempo_segments: list[TempoSegment] = field(default_factory=list)
    time_signature_segments: list[TimeSignatureSegment] = field(default_factory=list)


@dataclass
class MidiEvent:
    kind: EventKind
    tick: int
    seq: int
    channel: int | None = None
    note: int | None = None
    velocity: int | None = None
    control: int | None = None
    value: int | None = None
    program: int | None = None
    pitch: int | None = None
    pressure: int | None = None
    source: Literal["input", "synth"] = "input"


@dataclass
class NoteSpan:
    start_tick: int
    end_tick: int
    pitch: int
    channel: int
    velocity: int


@dataclass
class SessionModel:
    schema_name: str
    schema_version: str
    header: SessionHeader
    events: list[MidiEvent] = field(default_factory=list)
    note_spans: list[NoteSpan] = field(default_factory=list)
    active_notes: dict[tuple[int, int], list[tuple[int, int]]] = field(default_factory=dict)
    recent_hits: list[tuple[int, int, int, float]] = field(default_factory=list)
    cc_events: list[tuple[int, int, int, int, float]] = field(default_factory=list)
    cc_order: list[tuple[int, int]] = field(default_factory=list)
    export_path: str | None = None
    start_time: float | None = None
    stop_time: float | None = None
    _seq: int = 0

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def append_normalized_event(self, *, kind: EventKind, tick: int, source: Literal["input", "synth"] = "input", **fields: Any) -> MidiEvent:
        event = MidiEvent(kind=kind, tick=int(tick), seq=self.next_seq(), source=source, **fields)
        self.events.append(event)
        return event

    def append_event_from_message(self, tick: int, msg: mido.Message) -> MidiEvent | None:
        kind = str(getattr(msg, "type", ""))
        if kind not in {
            "note_on",
            "note_off",
            "control_change",
            "program_change",
            "pitchwheel",
            "aftertouch",
            "polytouch",
        }:
            return None

        channel = int(getattr(msg, "channel", 0)) + 1
        if kind == "pitchwheel":
            return self.append_normalized_event(kind="pitch_bend", tick=tick, channel=channel, pitch=int(getattr(msg, "pitch", 0)))
        if kind == "aftertouch":
            return self.append_normalized_event(
                kind="channel_pressure", tick=tick, channel=channel, pressure=int(getattr(msg, "value", 0))
            )
        if kind == "polytouch":
            return self.append_normalized_event(
                kind="poly_aftertouch",
                tick=tick,
                channel=channel,
                note=int(getattr(msg, "note", -1)),
                pressure=int(getattr(msg, "value", 0)),
            )
        if kind == "note_on":
            velocity = int(getattr(msg, "velocity", 0))
            normalized_kind: EventKind = "note_off" if velocity == 0 else "note_on"
            return self.append_normalized_event(
                kind=normalized_kind,
                tick=tick,
                channel=channel,
                note=int(getattr(msg, "note", -1)),
                velocity=velocity,
            )
        if kind == "note_off":
            return self.append_normalized_event(
                kind="note_off",
                tick=tick,
                channel=channel,
                note=int(getattr(msg, "note", -1)),
                velocity=int(getattr(msg, "velocity", 0)),
            )
        if kind == "control_change":
            return self.append_normalized_event(
                kind="control_change",
                tick=tick,
                channel=channel,
                control=int(getattr(msg, "control", -1)),
                value=int(getattr(msg, "value", 0)),
            )
        return self.append_normalized_event(
            kind="program_change",
            tick=tick,
            channel=channel,
            program=int(getattr(msg, "program", 0)),
        )

    def close_active_note(self, *, channel: int, note: int, end_tick: int, emit_synth_off: bool) -> None:
        key = (int(channel), int(note))
        active_stack = self.active_notes.get(key)
        if not active_stack:
            return
        start_tick, velocity = active_stack.pop()
        if not active_stack:
            self.active_notes.pop(key, None)
        end_tick = max(int(end_tick), int(start_tick))
        self.note_spans.append(
            NoteSpan(
                start_tick=int(start_tick),
                end_tick=int(end_tick),
                pitch=int(note),
                channel=int(channel),
                velocity=int(velocity),
            )
        )
        if emit_synth_off:
            self.append_normalized_event(
                kind="note_off",
                tick=end_tick,
                channel=int(channel),
                note=int(note),
                velocity=0,
                source="synth",
            )

    def flush_active_notes(self, *, end_tick: int, emit_synth_off: bool = True) -> None:
        for channel, note in list(self.active_notes.keys()):
            while self.active_notes.get((int(channel), int(note))):
                self.close_active_note(channel=channel, note=note, end_tick=end_tick, emit_synth_off=emit_synth_off)

    def close_channel_active_notes(self, *, channel: int, end_tick: int, emit_synth_off: bool = True) -> None:
        for ch, note in [key for key in self.active_notes if int(key[0]) == int(channel)]:
            while self.active_notes.get((int(ch), int(note))):
                self.close_active_note(channel=ch, note=note, end_tick=end_tick, emit_synth_off=emit_synth_off)

    def stop_flush(self, stop_tick: int) -> None:
        self.flush_active_notes(end_tick=stop_tick, emit_synth_off=True)
        self.header.stop_tick = max(int(stop_tick), int(self.header.start_tick))



def to_mido_message(event: MidiEvent) -> mido.Message | None:
    if event.kind == "note_on":
        return mido.Message("note_on", channel=max(0, int((event.channel or 1) - 1)), note=int(event.note or 0), velocity=int(event.velocity or 0), time=0)
    if event.kind == "note_off":
        return mido.Message("note_off", channel=max(0, int((event.channel or 1) - 1)), note=int(event.note or 0), velocity=int(event.velocity or 0), time=0)
    if event.kind == "control_change":
        return mido.Message("control_change", channel=max(0, int((event.channel or 1) - 1)), control=int(event.control or 0), value=int(event.value or 0), time=0)
    if event.kind == "program_change":
        return mido.Message("program_change", channel=max(0, int((event.channel or 1) - 1)), program=int(event.program or 0), time=0)
    if event.kind == "pitch_bend":
        return mido.Message("pitchwheel", channel=max(0, int((event.channel or 1) - 1)), pitch=int(event.pitch or 0), time=0)
    if event.kind == "channel_pressure":
        return mido.Message("aftertouch", channel=max(0, int((event.channel or 1) - 1)), value=int(event.pressure or 0), time=0)
    if event.kind == "poly_aftertouch":
        return mido.Message("polytouch", channel=max(0, int((event.channel or 1) - 1)), note=int(event.note or 0), value=int(event.pressure or 0), time=0)
    return None

def build_session_model(
    *,
    session_id: str,
    start_tick: int,
    bpm: float,
    ppqn: int = 24,
    tempo_segments: list[TempoSegment] | None = None,
    time_signature_segments: list[TimeSignatureSegment] | None = None,
) -> SessionModel:
    start_tick = int(start_tick)
    return SessionModel(
        schema_name=SCHEMA_NAME,
        schema_version=SCHEMA_VERSION,
        header=SessionHeader(
            session_id=str(session_id),
            start_tick=start_tick,
            stop_tick=start_tick,
            ppqn=int(ppqn),
            bpm=float(bpm if bpm > 0 else 120.0),
            tempo_segments=list(tempo_segments or []),
            time_signature_segments=list(time_signature_segments or []),
        ),
    )
