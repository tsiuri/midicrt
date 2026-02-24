from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import mido


@dataclass
class EngineState:
    tick_counter: int = 0
    bar_counter: int = 0
    running: bool = False
    bpm: float = 0.0
    last_clock_time: float | None = None
    clock_intervals: deque[float] = field(default_factory=lambda: deque(maxlen=24))


class MidiEngine:
    """In-process MIDI engine for transport state + event dispatch."""

    def __init__(
        self,
        plugins: list[Any] | None = None,
        pages: dict[int, Any] | None = None,
        get_current_page: Callable[[], int] | None = None,
        on_event: Callable[[dict[str, Any], mido.Message], None] | None = None,
    ) -> None:
        self.state = EngineState()
        self.plugins = plugins if plugins is not None else []
        self.pages = pages if pages is not None else {}
        self.get_current_page = get_current_page or (lambda: 1)
        self.on_event = on_event
        self._lock = threading.Lock()

    def get_snapshot(self) -> dict[str, Any]:
        with self._lock:
            snap = asdict(self.state)
        snap.pop("clock_intervals", None)
        module_state = {}
        for mod in self.plugins:
            getter = getattr(mod, "get_state", None)
            if callable(getter):
                try:
                    module_state[getattr(mod, "__name__", repr(mod))] = getter()
                except Exception:
                    pass
        snap["modules"] = module_state
        return snap

    def make_plugin_state(self, cols: int, rows: int, y_offset: int = 3) -> dict[str, Any]:
        snap = self.get_snapshot()
        return {
            "tick": snap["tick_counter"],
            "bar": snap["bar_counter"],
            "running": snap["running"],
            "bpm": snap["bpm"],
            "cols": cols,
            "rows": rows,
            "y_offset": y_offset,
        }

    def ingest(self, msg: mido.Message) -> dict[str, Any]:
        event = self._normalize_event(msg)
        self._update_transport(event)
        self._dispatch(event, msg)
        if self.on_event:
            try:
                self.on_event(event, msg)
            except Exception:
                pass
        return event

    def run_input_loop(self, port: Any, stop_flag: Callable[[], bool], sleep_s: float = 0.001) -> None:
        while not stop_flag():
            for msg in port.iter_pending():
                self.ingest(msg)
            time.sleep(sleep_s)

    def _normalize_event(self, msg: mido.Message) -> dict[str, Any]:
        payload = {"kind": msg.type, "timestamp": time.time(), "raw": msg}
        for key, value in vars(msg).items():
            if key.startswith("_"):
                continue
            payload[key] = value
        return payload

    def _update_transport(self, event: dict[str, Any]) -> None:
        with self._lock:
            kind = event["kind"]
            if kind == "start":
                self.state.running = True
                self.state.tick_counter = 0
                self.state.bar_counter = 0
                self.state.clock_intervals.clear()
                self.state.last_clock_time = None
            elif kind == "stop":
                self.state.running = False
            elif kind == "clock":
                if not self.state.running:
                    return
                self.state.tick_counter += 1
                if (self.state.tick_counter % (24 * 4)) == 0:
                    self.state.bar_counter += 1
                now = time.time()
                if self.state.last_clock_time is not None:
                    self.state.clock_intervals.append(now - self.state.last_clock_time)
                    if self.state.clock_intervals:
                        avg = sum(self.state.clock_intervals) / len(self.state.clock_intervals)
                        self.state.bpm = 60.0 / (24 * avg)
                self.state.last_clock_time = now

    def _dispatch(self, event: dict[str, Any], msg: mido.Message) -> None:
        kind = event["kind"]
        current_page = self.get_current_page()

        for mod in self.plugins:
            if hasattr(mod, "on_event"):
                try:
                    mod.on_event(event)
                except Exception:
                    pass
            if kind == "clock" and hasattr(mod, "on_tick"):
                try:
                    mod.on_tick(self.get_snapshot())
                except Exception:
                    pass
            if kind in ("sysex", "note_on", "note_off", "control_change", "program_change") and hasattr(mod, "handle"):
                try:
                    mod.handle(msg)
                except Exception:
                    pass

        page = self.pages.get(current_page)
        if page and hasattr(page, "handle") and kind in ("note_on", "note_off", "control_change", "program_change"):
            try:
                page.handle(msg)
            except Exception:
                pass

        for pid, pg in self.pages.items():
            if pid == current_page:
                continue
            if getattr(pg, "BACKGROUND", False) and hasattr(pg, "handle") and kind in (
                "note_on",
                "note_off",
                "control_change",
                "program_change",
            ):
                try:
                    pg.handle(msg)
                except Exception:
                    pass
