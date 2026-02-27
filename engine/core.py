from __future__ import annotations

import threading
import time
import os
import queue
from copy import deepcopy
from types import MappingProxyType
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import mido

from engine.legacy_page_router import LegacyPageRouter
from engine.modules.interfaces import EngineModule
from engine.scheduler import ModuleScheduler
from engine.state.schema import build_snapshot, normalize_deep_research_payload
from engine.deep_research.platform import resolve_feature_flags
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
    jitter_p95: float = 0.0
    jitter_p99: float = 0.0
    drift_ppm: float = 0.0
    interval_stats: dict[str, float] = field(default_factory=dict)
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
        deep_research_settings: dict[str, Any] | None = None,
        legacy_pages_provider: Callable[[], dict[int, Any]] | None = None,
        current_page_provider: Callable[[], int] | None = None,
        plugin_state_provider: Callable[[], dict[str, Any]] | None = None,
        midi_activity_handler: Callable[[mido.Message], None] | None = None,
        legacy_event_shim_enabled: bool = True,
        tempo_metrics: dict[str, Any] | None = None,
    ) -> None:
        self.state = EngineState()
        self.modules = modules if modules is not None else []
        self.on_event = on_event
        self.publisher = publisher
        self._lock = threading.Lock()
        _tm_cfg = tempo_metrics if isinstance(tempo_metrics, dict) else {}
        self._tempo_map = TempoMap(
            interval_window=max(2, int(_tm_cfg.get("interval_window", 24))),
            baseline_window=max(2, int(_tm_cfg.get("baseline_window", 96))),
            stats_window=max(2, int(_tm_cfg.get("stats_window", _tm_cfg.get("interval_window", 24)))),
        )
        self._capture_cfg = {
            "bars_to_keep": 16,
            "dump_bars": 4,
            "output_dir": "captures",
            "file_prefix": "capture",
            "default_bpm": 120.0,
            "quantize": "none",
        }
        self._capture_events = deque()
        self._capture_seq = 0
        self._last_capture_meta: dict[str, Any] = {
            "trigger": "",
            "export_path": "",
            "event_count": 0,
            "quantization_mode": "none",
            "effective_tempo_map_segment": {},
        }
        self._command_hooks = command_hooks if isinstance(command_hooks, dict) else {}
        self._deep_research_cfg = deep_research_settings if isinstance(deep_research_settings, dict) else {}
        self._deep_research_flags = resolve_feature_flags(self._deep_research_cfg)
        if not self._deep_research_cfg and isinstance(module_policies, dict):
            alt = module_policies.get("deep_research", {})
            if isinstance(alt, dict):
                self._deep_research_cfg = dict(alt)
        self._deep_research_flags = resolve_feature_flags(self._deep_research_cfg)
        self._scheduler = ModuleScheduler(
            module_policies=module_policies,
            overload_cost_ms=overload_cost_ms,
            deep_research_settings=self._deep_research_cfg,
        )
        self._deep_research_modules: set[str] = {
            str(v).strip().lower()
            for v in self._deep_research_cfg.get("modules", ["deepresearch", "deep_research"])
            if str(v).strip()
        }
        if not self._deep_research_modules:
            self._deep_research_modules = {"deepresearch", "deep_research"}
        self._deep_research_q: queue.Queue[tuple[str, Any, dict[str, Any], bool, dict[str, Any]]] = queue.Queue(
            maxsize=max(1, int(self._deep_research_cfg.get("queue_size", 1)))
        )
        self._deep_research_stop = threading.Event()
        self._deep_research_worker = threading.Thread(
            target=self._deep_research_loop,
            daemon=True,
            name="engine-deep-research",
        )
        self._deep_research_worker.start()
        self._legacy_pages_provider = legacy_pages_provider
        self._current_page_provider = current_page_provider
        self._plugin_state_provider = plugin_state_provider
        self._midi_activity_handler = midi_activity_handler
        self._legacy_event_shim_enabled = bool(legacy_event_shim_enabled)
        self._legacy_event_adapter = LegacyPageRouter(
            pages_provider=self._legacy_pages_provider,
            current_page_provider=self._current_page_provider,
            plugin_state_provider=self._plugin_state_provider,
            midi_activity_handler=self._midi_activity_handler,
            enabled=self._legacy_event_shim_enabled,
        )
        self._ui_context: dict[str, Any] = {"cols": 0, "rows": 0, "y_offset": 3, "current_page": 0}
        self._deep_research_late_policy = str(self._deep_research_cfg.get("late_policy", "drop")).strip().lower() or "drop"
        if self._deep_research_late_policy not in {"drop", "apply_next"}:
            self._deep_research_late_policy = "drop"
        self._deep_research_latest_snapshot_version = 0
        self._deep_research_outgoing: dict[str, Any] = {}
        self._deep_research_pending: dict[str, Any] | None = None
        self._diag_interval_s = 0.5
        self._diag_next_ts = 0.0

    def _freeze_snapshot_payload(self, payload: Any) -> Any:
        if isinstance(payload, dict):
            frozen = {str(key): self._freeze_snapshot_payload(value) for key, value in payload.items()}
            return MappingProxyType(frozen)
        if isinstance(payload, list):
            return tuple(self._freeze_snapshot_payload(value) for value in payload)
        if isinstance(payload, set):
            return tuple(sorted(self._freeze_snapshot_payload(value) for value in payload))
        if isinstance(payload, tuple):
            return tuple(self._freeze_snapshot_payload(value) for value in payload)
        return payload

    def _thaw_snapshot_payload(self, payload: Any) -> Any:
        if isinstance(payload, MappingProxyType):
            return {key: self._thaw_snapshot_payload(value) for key, value in payload.items()}
        if isinstance(payload, dict):
            return {key: self._thaw_snapshot_payload(value) for key, value in payload.items()}
        if isinstance(payload, tuple):
            return [self._thaw_snapshot_payload(value) for value in payload]
        return payload

    def _make_research_snapshot(self, event: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.get_snapshot()["schema"]
        transport = snapshot.get("transport", {}) if isinstance(snapshot.get("transport"), dict) else {}
        with self._lock:
            self._deep_research_latest_snapshot_version += 1
            version = self._deep_research_latest_snapshot_version
        timestamp = time.time()
        snapshot["deep_research"] = normalize_deep_research_payload(
            {
                "version": version,
                "timestamp": timestamp,
                "source_snapshot_version": version,
                "source_snapshot_timestamp": timestamp,
                "source_tick": int(transport.get("tick", 0)),
                "late_policy": self._deep_research_late_policy,
                "stale": False,
                "applied": False,
                "dropped": False,
                "drop_reason": "",
                "event_kind": str(event.get("kind", "")),
                "feature_flags": dict(self._deep_research_flags),
                "result": {},
            }
        )
        return {"schema": snapshot, "event": deepcopy(event)}

    def _apply_deep_research_result(self, source_version: int, source_timestamp: float, source_tick: int, result: Any) -> None:
        now_ts = time.time()
        include_metadata = self._deep_research_flags.get("enable_payload_metadata", True)
        include_result = self._deep_research_flags.get("enable_payload_result", True)
        payload = {
            "version": int(source_version),
            "timestamp": now_ts,
            "produced_at": now_ts,
            "source_snapshot_version": int(source_version),
            "source_snapshot_timestamp": float(source_timestamp),
            "source_tick": int(source_tick),
            "late_policy": self._deep_research_late_policy,
            "stale": False,
            "applied": True,
            "dropped": False,
            "drop_reason": "",
            "result": deepcopy(result) if include_result and isinstance(result, dict) else ({"value": deepcopy(result)} if include_result else {}),
        }
        if not include_metadata:
            payload["produced_at"] = 0.0
            payload["source_snapshot_version"] = 0
            payload["source_snapshot_timestamp"] = 0.0
            payload["source_tick"] = 0
            payload["lag_ms"] = 0.0
        else:
            payload["lag_ms"] = max(0.0, (now_ts - float(source_timestamp)) * 1000.0)
        with self._lock:
            if source_version < self._deep_research_latest_snapshot_version:
                payload["stale"] = True
                if self._deep_research_late_policy == "drop":
                    payload["applied"] = False
                    payload["dropped"] = True
                    payload["drop_reason"] = "late_result"
                    self._deep_research_outgoing = normalize_deep_research_payload(payload)
                    return
                self._deep_research_pending = normalize_deep_research_payload(payload)
                return
            self._deep_research_outgoing = normalize_deep_research_payload(payload)

    def get_snapshot(self) -> dict[str, Any]:
        with self._lock:
            snap = asdict(self.state)
            active_notes = {ch: set(notes) for ch, notes in self.state.active_notes.items()}

        module_state, views = self._collect_module_outputs()
        if not self._deep_research_flags.get("enable_ui_surface_module_outputs", True):
            module_state = {}
        if not self._deep_research_flags.get("enable_ui_surface_views", True):
            views = {}
        module_names = [getattr(mod, "name", mod.__class__.__name__) for mod in self.modules]
        scheduler_diag = self._scheduler.diagnostics(module_names)
        with self._lock:
            if (
                self._deep_research_pending
                and self._deep_research_late_policy == "apply_next"
                and self._deep_research_pending.get("source_snapshot_version", 0)
                <= self._deep_research_latest_snapshot_version
            ):
                self._deep_research_outgoing = dict(self._deep_research_pending)
                self._deep_research_pending = None
            deep_research_payload = dict(self._deep_research_outgoing)
        if not self._deep_research_flags.get("enable_ui_surface_deep_research", True):
            deep_research_payload = {}

        capture_meta = self._capture_metadata_snapshot()
        deep_metrics = scheduler_diag.get("modules", {}).get("deep_research", {}).get("metrics", {}) if isinstance(scheduler_diag, dict) else {}
        deep_mods = deep_metrics.get("modules", {}) if isinstance(deep_metrics, dict) else {}
        warn_cards = []
        for mod_name in sorted(deep_mods):
            info = deep_mods.get(mod_name, {}) if isinstance(deep_mods.get(mod_name), dict) else {}
            if not info.get("over_budget") and int(info.get("over_budget_count", 0)) <= 0 and int(info.get("skipped_due_degradation", 0)) <= 0:
                continue
            warn_cards.append({
                "name": str(mod_name),
                "status": "warn",
                "latency_ms": float(info.get("last_runtime_ms", 0.0)),
                "drop_rate": float(info.get("skipped_due_degradation", 0)),
                "detail": "deep_research_over_budget",
                "over_budget_count": int(info.get("over_budget_count", 0)),
            })
        if warn_cards:
            views.setdefault("module_health", {"cards": warn_cards})
        module_health = {
            "status": "degraded" if warn_cards else "ok",
            "updated_at": time.time(),
            "modules": deep_mods,
            "degradation_policy": deep_metrics.get("degradation_policy", "none") if isinstance(deep_metrics, dict) else "none",
        }
        schema_snapshot = build_snapshot(
            timestamp=time.time(),
            tick=snap["tick_counter"],
            bar=snap["bar_counter"],
            running=snap["running"],
            bpm=snap["bpm"],
            clock_interval_ms=snap.get("clock_interval_ms", 0.0),
            jitter_rms=snap.get("jitter_rms", 0.0),
            clock_jitter_rms=snap.get("jitter_rms", 0.0),
            clock_jitter_p95=snap.get("jitter_p95", 0.0),
            clock_drift_ppm=snap.get("drift_ppm", 0.0),
            microtiming_bins=snap.get("interval_stats", {}),
            microtiming_window_events=int((snap.get("interval_stats", {}) or {}).get("count", 0)),
            meter_estimate=snap.get("meter_estimate", "4/4"),
            confidence=snap.get("confidence", 0.0),
            active_notes=active_notes,
            module_outputs=module_state,
            views=views,
            status_text=snap.get("status_text", ""),
            diagnostics=scheduler_diag or snap.get("diagnostics", {}),
            retrospective_capture={
                "buffer_bars": int(self._capture_cfg.get("bars_to_keep", 0)),
                "events_buffered": int(len(self._capture_events)),
                "armed": False,
                "last_commit_path": str(capture_meta.get("export_path", "")),
                "capture_metadata": capture_meta,
            },
            module_health=module_health,
            ui_context=dict(self._ui_context),
            deep_research=deep_research_payload,
        ).as_dict()

        # Backward-compat fields while old callsites migrate.
        snap["modules"] = module_state
        snap["schema"] = schema_snapshot
        return snap

    def make_plugin_state(self, cols: int, rows: int, y_offset: int = 3) -> dict[str, Any]:
        # Fast path for high-frequency legacy/background hooks.
        # Avoid get_snapshot() here: it builds module views + scheduler diagnostics.
        with self._lock:
            tick = int(self.state.tick_counter)
            bar = int(self.state.bar_counter)
            is_running = bool(self.state.running)
            tempo_bpm = float(self.state.bpm)
        return {
            "tick": tick,
            "bar": bar,
            "running": is_running,
            "bpm": tempo_bpm,
            "cols": cols,
            "rows": rows,
            "y_offset": y_offset,
        }

    def get_transport_state(self) -> dict[str, Any]:
        """Lightweight transport/diag snapshot for per-event callbacks."""
        with self._lock:
            return {
                "tick_counter": int(self.state.tick_counter),
                "bar_counter": int(self.state.bar_counter),
                "running": bool(self.state.running),
                "bpm": float(self.state.bpm),
                "diagnostics": dict(self.state.diagnostics) if isinstance(self.state.diagnostics, dict) else {},
            }

    def get_clock_state(self) -> dict[str, Any]:
        """Minimal transport snapshot for high-frequency on_clock hooks."""
        with self._lock:
            tick = int(self.state.tick_counter)
            bar = int(self.state.bar_counter)
            is_running = bool(self.state.running)
            tempo_bpm = float(self.state.bpm)
        return {
            "tick_counter": tick,
            "bar_counter": bar,
            "running": is_running,
            "bpm": tempo_bpm,
            # Legacy aliases commonly expected by older hooks.
            "tick": tick,
            "bar": bar,
        }

    def get_active_notes(self) -> dict[int, set[int]]:
        """Cheap copy of active notes by channel for UI overlays."""
        with self._lock:
            return {int(ch): set(notes) for ch, notes in self.state.active_notes.items()}

    def ingest(self, msg: mido.Message) -> dict[str, Any]:
        event = self._normalize_event(msg)
        self._update_transport(event)
        self._capture_event(event, msg)
        self._run_modules(event)
        self._route_legacy_event(event)
        if self.on_event:
            try:
                self.on_event(event, msg)
            except Exception:
                pass
        if self.publisher:
            try:
                if self.publisher.wants_publish():
                    self.publisher.publish(self.get_snapshot()["schema"], force=True)
            except Exception:
                pass
        return event


    def set_ui_context(self, *, cols: int | None = None, rows: int | None = None, y_offset: int | None = None, current_page: int | None = None) -> None:
        if cols is not None:
            self._ui_context["cols"] = int(cols)
        if rows is not None:
            self._ui_context["rows"] = int(rows)
        if y_offset is not None:
            self._ui_context["y_offset"] = int(y_offset)
        if current_page is not None:
            self._ui_context["current_page"] = int(current_page)

    def configure_capture(self, cfg: dict[str, Any] | None) -> None:
        cfg = cfg if isinstance(cfg, dict) else {}
        bars_to_keep = int(cfg.get("bars_to_keep", self._capture_cfg["bars_to_keep"]))
        dump_bars = int(cfg.get("dump_bars", self._capture_cfg["dump_bars"]))
        output_dir = str(cfg.get("output_dir", self._capture_cfg["output_dir"]))
        file_prefix = str(cfg.get("file_prefix", self._capture_cfg["file_prefix"]))
        default_bpm = float(cfg.get("default_bpm", self._capture_cfg["default_bpm"]))
        quantize = str(cfg.get("quantize", self._capture_cfg.get("quantize", "none"))).strip().lower() or "none"
        if quantize not in {"none", "bar"}:
            quantize = "none"
        self._capture_cfg = {
            "bars_to_keep": max(1, bars_to_keep),
            "dump_bars": max(1, dump_bars),
            "output_dir": output_dir,
            "file_prefix": file_prefix,
            "default_bpm": max(20.0, default_bpm),
            "quantize": quantize,
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
            return True, {
                "message": message,
                "path": out_path,
                "bars": bars,
                "capture_metadata": self._capture_metadata_snapshot(),
            }

        if cmd == "commit_last_bars":
            bars = payload.get("bars")
            if bars is not None:
                try:
                    bars = max(1, int(bars))
                except Exception:
                    return False, {"code": "invalid-args", "message": "bars must be an integer >= 1"}
            ok, message, out_path = self.capture_recent_to_file(
                bars=bars,
                trigger="ipc:commit_last_bars",
                bar_aligned=True,
            )
            if not ok:
                return False, {"code": "capture-failed", "message": message}
            return True, {
                "message": message,
                "path": out_path,
                "bars": bars,
                "capture_metadata": self._capture_metadata_snapshot(),
            }

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

    def capture_recent_to_file(
        self,
        bars: int | None = None,
        trigger: str = "manual",
        bar_aligned: bool = False,
    ) -> tuple[bool, str, str | None]:
        cfg = dict(self._capture_cfg)
        bars = int(bars or cfg["dump_bars"])
        bars = max(1, min(bars, cfg["bars_to_keep"]))
        with self._lock:
            tick_now = int(self.state.tick_counter)
            bpm_now = float(self.state.bpm or 0.0)
            meter_estimate = str(self.state.meter_estimate or "4/4")

        ticks_per_bar = 24 * 4
        if bar_aligned:
            end_tick = max(0, (tick_now // ticks_per_bar) * ticks_per_bar)
            start_tick = max(0, end_tick - bars * ticks_per_bar)
        else:
            end_tick = tick_now
            start_tick = max(0, tick_now - bars * ticks_per_bar)
        now = time.time()
        bpm_ref = bpm_now if bpm_now > 0 else float(cfg["default_bpm"])
        wall_secs = max(2.0, bars * (240.0 / max(1.0, bpm_ref)))
        start_wall = now - wall_secs

        events = []
        for ev in list(self._capture_events):
            beat_tick = ev.get("beat_tick")
            ts = ev.get("timestamp", 0.0)
            in_beat_range = beat_tick is not None and start_tick <= int(beat_tick) < max(start_tick + 1, end_tick)
            in_wall_range = ts >= start_wall
            if in_beat_range or (not bar_aligned and in_wall_range):
                events.append(ev)

        if not events:
            self._record_capture_meta(
                trigger=trigger,
                export_path="",
                event_count=0,
                quantization_mode=cfg.get("quantize", "none"),
                tempo_segment={
                    "start_tick": start_tick,
                    "end_tick": end_tick,
                    "bpm": bpm_ref,
                    "meter_estimate": meter_estimate,
                    "ticks_per_bar": ticks_per_bar,
                    "bar_aligned": bool(bar_aligned),
                },
            )
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
        self._record_capture_meta(
            trigger=trigger,
            export_path=out_path,
            event_count=len([ev for ev in events if ev.get("msg") is not None]),
            quantization_mode=cfg.get("quantize", "none"),
            tempo_segment={
                "start_tick": start_tick,
                "end_tick": end_tick,
                "bpm": bpm_ref,
                "meter_estimate": meter_estimate,
                "ticks_per_bar": ticks_per_bar,
                "bar_aligned": bool(bar_aligned),
            },
        )
        return True, f"capture saved: {out_path}", out_path

    def _record_capture_meta(
        self,
        *,
        trigger: str,
        export_path: str,
        event_count: int,
        quantization_mode: str,
        tempo_segment: dict[str, Any],
    ) -> None:
        with self._lock:
            self._last_capture_meta = {
                "trigger": str(trigger),
                "export_path": str(export_path),
                "event_count": max(0, int(event_count)),
                "quantization_mode": str(quantization_mode or "none"),
                "effective_tempo_map_segment": dict(tempo_segment) if isinstance(tempo_segment, dict) else {},
            }

    def _capture_metadata_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._last_capture_meta)

    def run_input_loop(
        self,
        port: Any,
        stop_flag: Callable[[], bool],
        sleep_s: float = 0.001,
        reopen_port: Callable[[Any, int, Exception], Any] | None = None,
        on_port_status: Callable[[str], None] | None = None,
    ) -> None:
        recoverable_errors = (OSError, IOError, RuntimeError)
        current_port = port
        reconnect_attempt = 0

        while not stop_flag():
            try:
                for msg in current_port.iter_pending():
                    self.ingest(msg)
                time.sleep(sleep_s)
            except recoverable_errors as exc:
                reconnect_attempt += 1
                if on_port_status:
                    try:
                        on_port_status(
                            f"[MIDI] input failure attempt={reconnect_attempt} "
                            f"port={getattr(current_port, 'name', '<unknown>')} error={exc}"
                        )
                    except Exception:
                        pass

                if reopen_port is None:
                    raise

                try:
                    current_port = reopen_port(current_port, reconnect_attempt, exc)
                except recoverable_errors:
                    if stop_flag():
                        break
                    continue
        if current_port is not None:
            try:
                current_port.close()
            except Exception:
                pass

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
        module_state.setdefault("deep_research", {})["metrics"] = self._scheduler.deep_research_diag()
        module_state.setdefault("deep_research", {})["module_health"] = self._scheduler.deep_module_diag()
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
            self.state.jitter_p95 = tempo_snapshot.jitter_p95
            self.state.jitter_p99 = tempo_snapshot.jitter_p99
            self.state.drift_ppm = tempo_snapshot.drift_ppm
            self.state.interval_stats = dict(tempo_snapshot.interval_stats)

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
        clock_snapshot: dict[str, Any] | None = None
        for mod in self.modules:
            name = getattr(mod, "name", mod.__class__.__name__)
            names.append(name)
            lname = str(name).strip().lower()
            if lname in self._deep_research_modules:
                self._enqueue_deep_research(name, mod, event, kind == "clock")
                continue
            if not self._scheduler.should_run(name, kind):
                continue
            start_ts = self._scheduler.begin(name)
            try:
                mod.on_event(event)
            except Exception:
                pass
            if kind == "clock":
                try:
                    if clock_snapshot is None:
                        clock_snapshot = self.get_clock_state()
                    mod.on_clock(clock_snapshot)
                except Exception:
                    pass
            self._scheduler.end(name, start_ts)

        now = time.monotonic()
        if now >= self._diag_next_ts:
            self._diag_next_ts = now + self._diag_interval_s
            diag = self._scheduler.diagnostics(names)
            overloaded = diag.get("scheduler", {}).get("overloaded_modules", [])
            with self._lock:
                self.state.diagnostics = diag
                if overloaded:
                    self.state.status_text = f"overload:{','.join(overloaded[:3])}"

    def _enqueue_deep_research(self, module_name: str, mod: Any, event: dict[str, Any], include_clock: bool) -> None:
        if not self._deep_research_flags.get("enable_module_execution", True):
            return
        if not self._scheduler.should_run_deep_research(module_name):
            return
        research_ctx = self._make_research_snapshot(event)
        frozen_ctx = self._freeze_snapshot_payload(research_ctx)
        work_item = (str(module_name), mod, dict(event), bool(include_clock), frozen_ctx)
        try:
            self._deep_research_q.put_nowait(work_item)
        except queue.Full:
            self._scheduler.drop_deep_research_cycle("queue_full")

    def _deep_research_loop(self) -> None:
        while not self._deep_research_stop.is_set():
            try:
                module_name, mod, event, include_clock, frozen_ctx = self._deep_research_q.get(timeout=0.05)
            except queue.Empty:
                continue
            start_ts = self._scheduler.begin_deep_research()
            mod_start = time.monotonic()
            try:
                research_ctx = self._thaw_snapshot_payload(frozen_ctx)
                schema_snapshot = research_ctx.get("schema", {}) if isinstance(research_ctx, dict) else {}
                source = schema_snapshot.get("deep_research", {}) if isinstance(schema_snapshot, dict) else {}
                mod.on_event(event)
                if include_clock:
                    mod.on_clock(schema_snapshot)
                outputs = mod.get_outputs() if hasattr(mod, "get_outputs") else {}
                self._apply_deep_research_result(
                    int(source.get("source_snapshot_version", source.get("version", 0))),
                    float(source.get("source_snapshot_timestamp", source.get("timestamp", 0.0))),
                    int(source.get("source_tick", 0)),
                    outputs if isinstance(outputs, dict) else {},
                )
            except Exception:
                pass
            finally:
                mod_runtime_ms = max(0.0, (time.monotonic() - mod_start) * 1000.0)
                self._scheduler.end_deep_research_module(module_name, mod_runtime_ms)
                self._scheduler.end_deep_research(start_ts)
                self._deep_research_q.task_done()


    def _route_legacy_event(self, event: dict[str, Any]) -> None:
        self._legacy_event_adapter.route(event)

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
