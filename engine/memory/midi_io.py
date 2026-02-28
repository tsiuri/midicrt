from __future__ import annotations

import os
import time
from uuid import uuid4

import mido

from engine.memory import session_model
from engine.memory.session_model import SessionModel, TempoSegment, TimeSignatureSegment, build_session_model


def _event_sort_key(ev: session_model.MidiEvent) -> tuple[int, int, str, int]:
    # Deterministic canonical ordering: tick first, insertion sequence as stable fallback.
    return (int(ev.tick), int(ev.seq), str(ev.kind), int(ev.channel or 0))


def _scaled_tick(abs_tick: int, scale: float) -> int:
    return int(round(int(abs_tick) * float(scale)))


def _to_midi_tick(session_tick: int, start_tick: int, ticks_per_clock: float) -> int:
    return max(0, int(round((int(session_tick) - int(start_tick)) * float(ticks_per_clock))))


def _append_tempo_and_timesig_track(session: SessionModel, midi: mido.MidiFile) -> None:
    ticks_per_clock = midi.ticks_per_beat / 24.0
    start_tick = int(session.header.start_tick)

    meta_track = mido.MidiTrack()
    midi.tracks.append(meta_track)
    meta_track.append(mido.MetaMessage("track_name", name="midicrt tempo/time-signature", time=0))

    tempo_segments = list(session.header.tempo_segments or [])
    if not tempo_segments:
        tempo_segments = [TempoSegment(start_tick=start_tick, bpm=float(session.header.bpm or 120.0))]

    timesig_segments = list(session.header.time_signature_segments or [])

    merged_meta: list[tuple[int, int, mido.MetaMessage]] = []
    order = 0

    for seg in tempo_segments:
        bpm = float(seg.bpm if float(seg.bpm) > 0 else 120.0)
        merged_meta.append(
            (
                _to_midi_tick(int(seg.start_tick), start_tick, ticks_per_clock),
                order,
                mido.MetaMessage("set_tempo", tempo=int(mido.bpm2tempo(bpm)), time=0),
            )
        )
        order += 1

    for seg in timesig_segments:
        numerator = max(1, int(seg.numerator))
        denominator = max(1, int(seg.denominator))
        merged_meta.append(
            (
                _to_midi_tick(int(seg.start_tick), start_tick, ticks_per_clock),
                order,
                mido.MetaMessage("time_signature", numerator=numerator, denominator=denominator, time=0),
            )
        )
        order += 1

    merged_meta.sort(key=lambda item: (int(item[0]), int(item[1])))

    prev_tick = 0
    for abs_tick, _, msg in merged_meta:
        delta = max(0, int(abs_tick) - int(prev_tick))
        prev_tick = int(abs_tick)
        meta_track.append(msg.copy(time=delta))



def export_session_midi(session: SessionModel, out_path: str) -> str | None:
    events = sorted(list(session.events or []), key=_event_sort_key)
    if not events:
        return None

    midi = mido.MidiFile(ticks_per_beat=480)
    _append_tempo_and_timesig_track(session, midi)

    event_track = mido.MidiTrack()
    midi.tracks.append(event_track)
    event_track.append(mido.MetaMessage("track_name", name="midicrt engine memory", time=0))

    ticks_per_clock = midi.ticks_per_beat / 24.0
    start_tick = int(session.header.start_tick)
    prev_tick = 0

    for event in events:
        rel = _to_midi_tick(int(event.tick), start_tick, ticks_per_clock)
        delta = max(0, rel - prev_tick)
        prev_tick = rel
        msg = session_model.to_mido_message(event)
        if msg is not None:
            event_track.append(msg.copy(time=delta))

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    tmp = f"{out_path}.tmp-{os.getpid()}-{time.time_ns()}"
    try:
        midi.save(tmp)
        os.replace(tmp, out_path)
        return out_path
    except Exception:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass
        return None


def import_midi_file(path: str, *, session_id: str | None = None, target_ppqn: int = 24) -> SessionModel | None:
    try:
        mid = mido.MidiFile(path)
    except Exception:
        return None

    tpb = max(1, int(mid.ticks_per_beat or 480))
    scale = float(target_ppqn) / float(tpb)
    bpm = 120.0

    session = build_session_model(
        session_id=session_id or f"import-{uuid4().hex[:12]}",
        start_tick=0,
        bpm=bpm,
        ppqn=int(target_ppqn),
    )
    session.start_time = time.time()
    session.stop_time = session.start_time

    merged: list[tuple[int, int, mido.Message]] = []
    merged_order = 0
    tempo_segments: list[TempoSegment] = []
    timesig_segments: list[TimeSignatureSegment] = []

    for tr in mid.tracks:
        abs_tick = 0
        for msg in tr:
            abs_tick += int(getattr(msg, "time", 0) or 0)
            if getattr(msg, "is_meta", False):
                if msg.type == "set_tempo":
                    try:
                        bpm = float(mido.tempo2bpm(int(msg.tempo)))
                    except Exception:
                        bpm = 120.0
                    tick = _scaled_tick(abs_tick, scale)
                    tempo_segments.append(TempoSegment(start_tick=tick, bpm=float(bpm)))
                elif msg.type == "time_signature":
                    tick = _scaled_tick(abs_tick, scale)
                    timesig_segments.append(
                        TimeSignatureSegment(
                            start_tick=tick,
                            numerator=max(1, int(getattr(msg, "numerator", 4))),
                            denominator=max(1, int(getattr(msg, "denominator", 4))),
                        )
                    )
                continue
            merged.append((abs_tick, merged_order, msg))
            merged_order += 1

    if tempo_segments:
        tempo_segments.sort(key=lambda seg: int(seg.start_tick))
        session.header.tempo_segments = tempo_segments
        session.header.bpm = float(tempo_segments[0].bpm)

    if timesig_segments:
        timesig_segments.sort(key=lambda seg: int(seg.start_tick))
        session.header.time_signature_segments = timesig_segments

    merged.sort(key=lambda item: (int(item[0]), int(item[1])))

    for abs_tick, _, msg in merged:
        tick = _scaled_tick(abs_tick, scale)
        mtype = str(getattr(msg, "type", ""))
        if mtype not in {"note_on", "note_off", "control_change", "program_change", "pitchwheel", "aftertouch", "polytouch"}:
            continue
        session.append_event_from_message(tick, msg)

        if mtype == "note_on":
            ch = int(getattr(msg, "channel", 0)) + 1
            note = int(getattr(msg, "note", -1))
            vel = int(getattr(msg, "velocity", 0))
            if vel > 0:
                session.active_notes.setdefault((ch, note), []).append((tick, vel))
            else:
                session.close_active_note(channel=ch, note=note, end_tick=tick, emit_synth_off=False)
        elif mtype == "note_off":
            ch = int(getattr(msg, "channel", 0)) + 1
            note = int(getattr(msg, "note", -1))
            session.close_active_note(channel=ch, note=note, end_tick=tick, emit_synth_off=False)
        elif mtype == "control_change" and int(getattr(msg, "control", -1)) == 123:
            ch = int(getattr(msg, "channel", 0)) + 1
            session.close_channel_active_notes(channel=ch, end_tick=tick, emit_synth_off=False)

    stop_tick = max([int(session.header.start_tick)] + [int(ev.tick) for ev in session.events])
    session.flush_active_notes(end_tick=stop_tick, emit_synth_off=False)
    session.header.stop_tick = stop_tick
    session.stop_time = time.time()
    return session
