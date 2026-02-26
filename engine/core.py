from __future__ import annotations

import threading
import time
import os
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import mido

from engine.modules.interfaces import EngineModule
from engine.scheduler import ModuleScheduler
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
    diagnostics: dict[str, Any] = field(default_factory=dict)


class MidiEngine:
    """In-process MIDI engine for transport state + analysis module execution."""

    def __init__(
        self,
        modules: list[EngineModule] | None = None,
        on_event: Callable[[dict[str, Any], mido.Message], None] | None = None,
        publisher: Any | None = None,
        command_hooks: dict[str, Callable[..., Any]] | None = None,
        module_policies: dict[str, dict[str, Any]] | None = None,
        overload_cost_ms: float = 6.0,
    ) -> None:
        self.state = EngineState()
        self.modules = modules if modules is not None else []
        self.on_event = on_event
        self.publisher = publisher
        self._lock = threading.Lock()
        self._tempo_map = TempoMap()
        self._capture_cfg = {
            "bars_to_keep": 16,
            "dump_bars": 4,
            "output_dir": "captures",
            "file_prefix": "capture",
            "default_bpm": 120.0,
        }
        self._capture_events = deque()
        self._capture_seq = 0
        self._command_hooks = command_hooks if isinstance(command_hooks, dict) else {}
        self._scheduler = ModuleScheduler(module_policies=module_policies, overload_cost_ms=overload_cost_ms)

    def get_snapshot(self) -> dict[str, Any]:
        with self._lock:
            snap = asdict(self.state)
            active_notes = {ch: set(notes) for ch, notes in self.state.active_notes.items()}

        module_state, views = self._collect_module_outputs()
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
            views=views,
            status_text=snap.get("status_text", ""),
            diagnostics=snap.get("diagnostics", {}),
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
        self._capture_event(event, msg)
        self._run_modules(event)
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

    def configure_capture(self, cfg: dict[str, Any] | None) -> None:
        cfg = cfg if isinstance(cfg, dict) else {}
        bars_to_keep = int(cfg.get("bars_to_keep", self._capture_cfg["bars_to_keep"]))
        dump_bars = int(cfg.get("dump_bars", self._capture_cfg["dump_bars"]))
        output_dir = str(cfg.get("output_dir", self._capture_cfg["output_dir"]))
        file_prefix = str(cfg.get("file_prefix", self._capture_cfg["file_prefix"]))
        default_bpm = float(cfg.get("default_bpm", self._capture_cfg["default_bpm"]))
        self._capture_cfg = {
            "bars_to_keep": max(1, bars_to_keep),
            "dump_bars": max(1, dump_bars),
            "output_dir": output_dir,
            "file_prefix": file_prefix,
            "default_bpm": max(20.0, default_bpm),
        }

    def set_status_text(self, text: str) -> None:
        with self._lock:
            self.state.status_text = str(text)

    def handle_command(self, command: str, args: dict[str, Any] | None = None) -> tuple[bool, dict[str, Any]]:
        """Dispatch IPC/local commands and return a structured result payload."""
        cmd = str(command or "").strip()
        payload = args if isinstance(args, dict) else {}

        if cmd == "capture_recent":
            bars = payload.get("bars")
            if bars is not None:
                try:
                    bars = max(1, int(bars))
                except Exception:
                    return False, {"code": "invalid-args", "message": "bars must be an integer >= 1"}
            ok, message, out_path = self.capture_recent_to_file(bars=bars, trigger="ipc:capture_recent")
            if not ok:
                return False, {"code": "capture-failed", "message": message}
            return True, {"message": message, "path": out_path, "bars": bars}

        if cmd == "set_page":
            hook = self._command_hooks.get("set_page")
            if not callable(hook):
                return False, {"code": "unsupported", "message": "set_page unavailable"}
            if "page" not in payload:
                return False, {"code": "invalid-args", "message": "missing page"}
            ok, resolved = hook(payload.get("page"))
            if not ok:
                return False, {"code": "invalid-page", "message": f"invalid page {resolved}", "page": resolved}
            return True, {"message": f"page->{resolved}", "page": resolved}

        if cmd == "wake_screensaver":
            hook = self._command_hooks.get("wake_screensaver")
            if not callable(hook):
                return False, {"code": "unsupported", "message": "wake_screensaver unavailable"}
            active = bool(hook())
            return True, {"message": "screen-on", "was_active": active}

        if cmd == "set_config":
            hook = self._command_hooks.get("set_config")
            if not callable(hook):
                return False, {"code": "unsupported", "message": "set_config unavailable"}
            section = payload.get("section")
            value = payload.get("value")
            if not isinstance(section, str) or not section:
                return False, {"code": "invalid-args", "message": "section must be a non-empty string"}
            if not isinstance(value, dict):
                return False, {"code": "invalid-args", "message": "value must be an object"}
            hook(section, value)
            return True, {"message": f"config[{section}] updated", "section": section}

        return False, {"code": "unknown-command", "message": f"unknown command {cmd}"}

    def capture_recent_to_file(self, bars: int | None = None, trigger: str = "manual") -> tuple[bool, str, str | None]:
        cfg = dict(self._capture_cfg)
        bars = int(bars or cfg["dump_bars"])
        bars = max(1, min(bars, cfg["bars_to_keep"]))
        with self._lock:
            tick_now = int(self.state.tick_counter)
            bpm_now = float(self.state.bpm or 0.0)

        ticks_per_bar = 24 * 4
        start_tick = max(0, tick_now - bars * ticks_per_bar)
        now = time.time()
        bpm_ref = bpm_now if bpm_now > 0 else float(cfg["default_bpm"])
        wall_secs = max(2.0, bars * (240.0 / max(1.0, bpm_ref)))
        start_wall = now - wall_secs

        events = []
        for ev in list(self._capture_events):
            beat_tick = ev.get("beat_tick")
            ts = ev.get("timestamp", 0.0)
            in_beat_range = beat_tick is not None and int(beat_tick) >= start_tick
            in_wall_range = ts >= start_wall
            if in_beat_range or in_wall_range:
                events.append(ev)

        if not events:
            return False, f"capture failed: no events in last {bars} bars", None

        midi = mido.MidiFile(ticks_per_beat=480)
        track = mido.MidiTrack()
        midi.tracks.append(track)
        tempo = mido.bpm2tempo(bpm_ref)
        track.append(mido.MetaMessage("set_tempo", tempo=int(tempo), time=0))
        track.append(mido.MetaMessage("track_name", name=f"midicrt {trigger}", time=0))

        ticks_per_clock = midi.ticks_per_beat / 24.0
        ticks_per_second = (bpm_ref / 60.0) * midi.ticks_per_beat
        events.sort(key=lambda item: (item.get("beat_tick") is None, item.get("beat_tick", 0), item.get("timestamp", 0.0)))
        prev_abs_tick = 0
        for ev in events:
            msg_obj = ev.get("msg")
            if msg_obj is None:
                continue
            beat_tick = ev.get("beat_tick")
            if beat_tick is not None:
                abs_tick = int(max(0, (int(beat_tick) - start_tick) * ticks_per_clock))
            else:
                abs_tick = int(max(0.0, (ev.get("timestamp", now) - start_wall) * ticks_per_second))
            delta = max(0, abs_tick - prev_abs_tick)
            prev_abs_tick = abs_tick
            track.append(msg_obj.copy(time=delta))

        output_dir = cfg["output_dir"]
        if not os.path.isabs(output_dir):
            output_dir = os.path.join(os.getcwd(), output_dir)
        os.makedirs(output_dir, exist_ok=True)
        self._capture_seq += 1
        stamp = time.strftime("%Y%m%d-%H%M%S")
        fname = f"{cfg['file_prefix']}-{stamp}-{trigger}-{self._capture_seq:04d}.mid"
        out_path = os.path.join(output_dir, fname)
        midi.save(out_path)
        return True, f"capture saved: {out_path}", out_path

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

    def _collect_module_outputs(self) -> tuple[dict[str, Any], dict[str, Any]]:
        module_state: dict[str, Any] = {}
        views: dict[str, Any] = {}
        for mod in self.modules:
            try:
                outputs = mod.get_outputs()
            except Exception:
                continue
            if not isinstance(outputs, dict):
                continue

            name = getattr(mod, "name", mod.__class__.__name__)
            if outputs:
                module_state[name] = dict(outputs)

            diag = self._scheduler.module_diag(name)
            if diag:
                module_state.setdefault(name, {})["timing"] = diag

            mod_views = outputs.get("views")
            if isinstance(mod_views, dict):
                for key, payload in mod_views.items():
                    if payload is not None:
                        views[str(key)] = payload
        return module_state, views

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
        for mod in self.modules:
            try:
                outputs = mod.get_outputs()
            except Exception:
                continue
            if not isinstance(outputs, dict):
                continue
            for field in ("timesig", "timesig_exp"):
                value = outputs.get(field)
                if isinstance(value, dict) and value.get("labels"):
                    candidates.append(value)
        return candidates

    def _run_modules(self, event: dict[str, Any]) -> None:
        kind = event["kind"]
        names: list[str] = []
        for mod in self.modules:
            name = getattr(mod, "name", mod.__class__.__name__)
            names.append(name)
            if not self._scheduler.should_run(name, kind):
                continue
            start_ts = self._scheduler.begin(name)
            try:
                mod.on_event(event)
            except Exception:
                pass
            if kind == "clock":
                try:
                    mod.on_clock(self.get_snapshot())
                except Exception:
                    pass
            self._scheduler.end(name, start_ts)

        diag = self._scheduler.diagnostics(names)
        overloaded = diag.get("scheduler", {}).get("overloaded_modules", [])
        with self._lock:
            self.state.diagnostics = diag
            if overloaded:
                self.state.status_text = f"overload:{','.join(overloaded[:3])}"

    def _capture_event(self, event: dict[str, Any], msg: mido.Message) -> None:
        if event["kind"] in ("clock", "start", "stop", "continue", "active_sensing"):
            return
        try:
            msg_copy = mido.Message.from_bytes(msg.bytes())
        except Exception:
            try:
                msg_copy = msg.copy()
            except Exception:
                return

        with self._lock:
            beat_tick = int(self.state.tick_counter)
            self._capture_events.append({
                "timestamp": float(event.get("timestamp", time.time())),
                "beat_tick": beat_tick,
                "kind": event.get("kind"),
                "msg": msg_copy,
            })
            min_tick = max(0, beat_tick - int(self._capture_cfg["bars_to_keep"]) * (24 * 4))
            cutoff = time.time() - (int(self._capture_cfg["bars_to_keep"]) * 8.0)
            while self._capture_events:
                head = self._capture_events[0]
                if int(head.get("beat_tick", 0)) >= min_tick and float(head.get("timestamp", 0.0)) >= cutoff:
                    break
                self._capture_events.popleft()
