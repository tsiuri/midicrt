from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import mido

from engine.state.schema import build_snapshot
from engine.state.tempo_map import TempoMap


@dataclass
class EngineState:
    tick_counter: int = 0
    bar_counter: int = 0
    running: bool = False
    bpm: float = 0.0
    active_notes: dict[int, set[int]] = field(default_factory=dict)
    status_text: str = "idle"
    clock_interval_ms: float = 0.0
    jitter_rms: float = 0.0
    meter_estimate: str = "4/4"
    confidence: float = 0.0


class MidiEngine:
    """In-process MIDI engine for transport state + event dispatch."""

    def __init__(
        self,
        plugins: list[Any] | None = None,
        pages: dict[int, Any] | None = None,
        get_current_page: Callable[[], int] | None = None,
        on_event: Callable[[dict[str, Any], mido.Message], None] | None = None,
        publisher: Any | None = None,
    ) -> None:
        self.state = EngineState()
        self.plugins = plugins if plugins is not None else []
        self.pages = pages if pages is not None else {}
        self.get_current_page = get_current_page or (lambda: 1)
        self.on_event = on_event
        self.publisher = publisher
        self._lock = threading.Lock()
        self._tempo_map = TempoMap()

    def get_snapshot(self) -> dict[str, Any]:
        with self._lock:
            snap = asdict(self.state)
            active_notes = {ch: set(notes) for ch, notes in self.state.active_notes.items()}

        module_state = {}
        for mod in self.plugins:
            getter = getattr(mod, "get_state", None)
            if callable(getter):
                try:
                    module_state[getattr(mod, "__name__", repr(mod))] = getter()
                except Exception:
                    pass
        schema_snapshot = build_snapshot(
            timestamp=time.time(),
            tick=snap["tick_counter"],
            bar=snap["bar_counter"],
            running=snap["running"],
            bpm=snap["bpm"],
            clock_interval_ms=snap.get("clock_interval_ms", 0.0),
            jitter_rms=snap.get("jitter_rms", 0.0),
            meter_estimate=snap.get("meter_estimate", "4/4"),
            confidence=snap.get("confidence", 0.0),
            active_notes=active_notes,
            module_outputs=module_state,
            status_text=snap.get("status_text", ""),
        ).as_dict()

        # Backward-compat fields while old callsites migrate.
        snap["modules"] = module_state
        snap["schema"] = schema_snapshot
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
        if self.publisher:
            try:
                self.publisher.publish(self.get_snapshot()["schema"])
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
            meter_candidates = self._collect_meter_candidates()
            self._tempo_map.handle(kind, event["timestamp"], meter_candidates=meter_candidates)
            tempo_snapshot = self._tempo_map.snapshot()

            self.state.running = tempo_snapshot.running
            self.state.tick_counter = tempo_snapshot.tick_counter
            self.state.bar_counter = tempo_snapshot.bar_counter
            self.state.bpm = tempo_snapshot.bpm
            self.state.clock_interval_ms = tempo_snapshot.clock_interval_ms
            self.state.jitter_rms = tempo_snapshot.jitter_rms
            self.state.meter_estimate = tempo_snapshot.meter_estimate
            self.state.confidence = tempo_snapshot.confidence

            if kind == "start":
                self.state.status_text = "running"
            elif kind == "stop":
                self.state.status_text = "stopped"
            elif kind in ("note_on", "note_off"):
                channel = int(event.get("channel", 0))
                note = int(event.get("note", -1))
                if note >= 0:
                    if channel not in self.state.active_notes:
                        self.state.active_notes[channel] = set()
                    if kind == "note_on" and int(event.get("velocity", 0)) > 0:
                        self.state.active_notes[channel].add(note)
                    else:
                        self.state.active_notes[channel].discard(note)
                self.state.status_text = f"{kind} ch={channel} note={note}"

    def _collect_meter_candidates(self) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for mod in self.plugins:
            for getter_name in ("get_timesig", "get_timesig_exp"):
                getter = getattr(mod, getter_name, None)
                if not callable(getter):
                    continue
                try:
                    value = getter()
                except Exception:
                    continue
                if isinstance(value, dict) and value.get("labels"):
                    candidates.append(value)
        return candidates

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
