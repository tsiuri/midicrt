from __future__ import annotations

import os
import time
from uuid import uuid4

import mido

from engine.memory import session_model
from engine.memory.session_model import SessionModel, build_session_model


def _event_sort_key(ev: session_model.MidiEvent) -> tuple[int, int, str, int]:
    # seq is primary deterministic order for canonical stream; remaining keys are tie-breakers for safety.
    return (int(ev.tick), int(ev.seq), str(ev.kind), int(ev.channel or 0))


def export_session_midi(session: SessionModel, out_path: str) -> str | None:
    events = sorted(list(session.events or []), key=_event_sort_key)
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
    prev_tick = 0

    for event in events:
        rel = max(0, int((int(event.tick) - start_tick) * ticks_per_clock))
        delta = max(0, rel - prev_tick)
        prev_tick = rel
        msg = session_model.to_mido_message(event)
        if msg is not None:
            track.append(msg.copy(time=delta))

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

    merged: list[tuple[int, mido.Message]] = []
    for tr in mid.tracks:
        abs_tick = 0
        for msg in tr:
            abs_tick += int(getattr(msg, "time", 0) or 0)
            if getattr(msg, "is_meta", False):
                if msg.type == "set_tempo":
                    try:
                        bpm = float(mido.tempo2bpm(int(msg.tempo)))
                        session.header.bpm = bpm
                    except Exception:
                        pass
                continue
            merged.append((abs_tick, msg))

    merged.sort(key=lambda item: (int(item[0]), str(getattr(item[1], "type", ""))))

    for abs_tick, msg in merged:
        tick = int(round(abs_tick * scale))
        mtype = str(getattr(msg, "type", ""))
        if mtype not in {"note_on", "note_off", "control_change", "program_change", "pitchwheel", "aftertouch", "polytouch"}:
            continue
        session.append_event_from_message(tick, msg)

        if mtype == "note_on":
            ch = int(getattr(msg, "channel", 0)) + 1
            note = int(getattr(msg, "note", -1))
            vel = int(getattr(msg, "velocity", 0))
            if vel > 0:
                session.close_active_note(channel=ch, note=note, end_tick=tick, emit_synth_off=False)
                session.active_notes[(ch, note)] = (tick, vel)
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
