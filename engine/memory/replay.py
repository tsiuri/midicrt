from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

import mido

from engine.memory.session_model import MidiEvent, SessionModel, to_mido_message

ReplayState = Literal["idle", "armed", "playing", "stopped", "loop"]


@dataclass
class ReplayStatus:
    state: ReplayState = "idle"
    session_id: str = ""
    tick_cursor: int = 0
    playing: bool = False
    loop_start_tick: int | None = None
    loop_end_tick: int | None = None

    def as_dict(self) -> dict[str, int | bool | str | None]:
        return {
            "state": self.state,
            "session_id": self.session_id,
            "tick_cursor": int(self.tick_cursor),
            "playing": bool(self.playing),
            "loop_start_tick": self.loop_start_tick,
            "loop_end_tick": self.loop_end_tick,
        }


class ReplayController:
    """Transport-tick-driven in-engine session replay scheduler."""

    def __init__(self, *, emit_midi: Callable[[mido.Message], None]) -> None:
        self._emit_midi = emit_midi
        self._status = ReplayStatus()
        self._session: SessionModel | None = None
        self._events_by_tick: dict[int, list[MidiEvent]] = {}
        self._session_start_tick = 0
        self._start_offset = 0
        self._end_offset = 0
        self._play_offset = 0
        self._loop_start_offset: int | None = None
        self._loop_end_offset: int | None = None
        self._engine_tick_anchor: int | None = None
        self._elapsed_ticks = 0

    def start(
        self,
        *,
        session: SessionModel,
        engine_tick: int,
        running: bool,
        loop_start_tick: int | None = None,
        loop_end_tick: int | None = None,
    ) -> bool:
        self._session = session
        self._session_start_tick = int(session.header.start_tick)
        self._events_by_tick = self._index_events(session)

        event_ticks = sorted(self._events_by_tick.keys())
        if event_ticks:
            self._start_offset = max(0, int(event_ticks[0]) - self._session_start_tick)
            self._end_offset = max(self._start_offset, int(event_ticks[-1]) - self._session_start_tick)
        else:
            self._start_offset = 0
            self._end_offset = 0

        self._loop_start_offset, self._loop_end_offset = self._normalize_loop_bounds(loop_start_tick, loop_end_tick)
        if self._loop_start_offset is not None:
            self._play_offset = int(self._loop_start_offset)
        else:
            self._play_offset = int(self._start_offset)

        self._engine_tick_anchor = int(engine_tick)
        self._elapsed_ticks = 0
        self._status = ReplayStatus(
            state="playing" if running and self._loop_start_offset is None else ("loop" if running else "armed"),
            session_id=str(session.header.session_id),
            tick_cursor=int(self._session_start_tick + self._play_offset),
            playing=bool(running),
            loop_start_tick=(self._session_start_tick + self._loop_start_offset) if self._loop_start_offset is not None else None,
            loop_end_tick=(self._session_start_tick + self._loop_end_offset) if self._loop_end_offset is not None else None,
        )

        if running:
            self._emit_current_tick_events()
        return True

    def stop(self) -> None:
        if self._status.state == "idle":
            return
        self._status.state = "stopped"
        self._status.playing = False
        self._elapsed_ticks = 0

    def set_loop_region(self, *, start_tick: int | None, end_tick: int | None) -> None:
        self._loop_start_offset, self._loop_end_offset = self._normalize_loop_bounds(start_tick, end_tick)
        if self._loop_start_offset is None:
            self._status.loop_start_tick = None
            self._status.loop_end_tick = None
            if self._status.state == "loop":
                self._status.state = "playing"
            return
        self._play_offset = int(self._loop_start_offset)
        self._status.loop_start_tick = self._session_start_tick + int(self._loop_start_offset)
        self._status.loop_end_tick = self._session_start_tick + int(self._loop_end_offset)
        if self._status.playing:
            self._status.state = "loop"

    def on_transport(self, *, tick: int, running: bool) -> None:
        state = self._status.state
        if state in {"idle", "stopped"}:
            return
        if state == "armed":
            if not running:
                return
            self._engine_tick_anchor = int(tick)
            self._elapsed_ticks = 0
            self._status.playing = True
            self._status.state = "loop" if self._loop_start_offset is not None else "playing"
            self._emit_current_tick_events()
            return
        if not running:
            self._status.playing = False
            return

        if self._engine_tick_anchor is None:
            self._engine_tick_anchor = int(tick)

        target_delta = max(0, int(tick) - int(self._engine_tick_anchor))
        step_count = max(0, target_delta - int(self._elapsed_ticks))
        for _ in range(step_count):
            if not self._advance_one_tick():
                break

        self._status.playing = True
        self._status.tick_cursor = self._session_start_tick + int(self._play_offset)

    def status(self) -> dict[str, int | bool | str | None]:
        return self._status.as_dict()

    def _index_events(self, session: SessionModel) -> dict[int, list[MidiEvent]]:
        ordered = list(enumerate(session.events or []))
        ordered.sort(key=lambda item: (int(item[1].tick), int(item[1].seq), int(item[0])))
        out: dict[int, list[MidiEvent]] = {}
        for _, event in ordered:
            out.setdefault(int(event.tick), []).append(event)
        return out

    def _normalize_loop_bounds(self, start_tick: int | None, end_tick: int | None) -> tuple[int | None, int | None]:
        if start_tick is None and end_tick is None:
            return None, None
        low = int(start_tick if start_tick is not None else self._session_start_tick)
        high = int(end_tick if end_tick is not None else (self._session_start_tick + self._end_offset + 1))
        if high <= low:
            high = low + 1
        start_offset = max(self._start_offset, low - self._session_start_tick)
        end_offset = min(max(start_offset + 1, high - self._session_start_tick), self._end_offset + 1)
        return int(start_offset), int(end_offset)

    def _advance_one_tick(self) -> bool:
        if self._loop_start_offset is not None and self._loop_end_offset is not None:
            next_offset = int(self._play_offset) + 1
            if next_offset >= int(self._loop_end_offset):
                next_offset = int(self._loop_start_offset)
            self._play_offset = next_offset
            self._elapsed_ticks += 1
            self._emit_current_tick_events()
            self._status.state = "loop"
            return True

        if int(self._play_offset) >= int(self._end_offset):
            self.stop()
            return False

        self._play_offset += 1
        self._elapsed_ticks += 1
        self._emit_current_tick_events()
        self._status.state = "playing"
        return True

    def _emit_current_tick_events(self) -> None:
        tick = self._session_start_tick + int(self._play_offset)
        for event in self._events_by_tick.get(tick, []):
            msg = to_mido_message(event)
            if msg is not None:
                self._emit_midi(msg)
        self._status.tick_cursor = tick
