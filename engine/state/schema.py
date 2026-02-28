from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 5


@dataclass
class TransportSnapshot:
    tick: int
    bar: int
    running: bool
    bpm: float
    clock_interval_ms: float = 0.0
    jitter_rms: float = 0.0
    quality: dict[str, float] = field(default_factory=dict)
    microtiming: dict[str, Any] = field(default_factory=dict)
    meter_estimate: str = "4/4"
    confidence: float = 0.0


@dataclass
class ChannelSnapshot:
    channel: int
    active_notes: list[int] = field(default_factory=list)


@dataclass
class MemorySnapshot:
    armed: bool = False
    current_id: str = ""
    session_count: int = 0
    max_sessions: int = 0
    replay_playing: bool = False
    replay_session_id: str = ""
    replay_tick: int = 0


@dataclass
class StateSnapshot:
    schema_version: int
    timestamp: float
    transport: TransportSnapshot
    channels: list[ChannelSnapshot] = field(default_factory=list)
    active_notes: dict[int, list[int]] = field(default_factory=dict)
    module_outputs: dict[str, Any] = field(default_factory=dict)
    views: dict[str, Any] | None = None
    status_text: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)
    retrospective_capture: dict[str, Any] = field(default_factory=dict)
    module_health: dict[str, Any] = field(default_factory=dict)
    ui_context: dict[str, Any] = field(default_factory=dict)
    deep_research: dict[str, Any] | None = None
    memory: MemorySnapshot = field(default_factory=MemorySnapshot)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "timestamp": self.timestamp,
            "transport": {
                "tick": self.transport.tick,
                "bar": self.transport.bar,
                "running": self.transport.running,
                "bpm": self.transport.bpm,
                "clock_interval_ms": self.transport.clock_interval_ms,
                "jitter_rms": self.transport.jitter_rms,
                "quality": deepcopy(self.transport.quality),
                "microtiming": deepcopy(self.transport.microtiming),
                "meter_estimate": self.transport.meter_estimate,
                "confidence": self.transport.confidence,
            },
            "channels": [
                {"channel": ch.channel, "active_notes": list(ch.active_notes)} for ch in self.channels
            ],
            "active_notes": {str(k): list(v) for k, v in self.active_notes.items()},
            "module_outputs": self.module_outputs,
            "status_text": self.status_text,
            "diagnostics": self.diagnostics,
            "retrospective_capture": self.retrospective_capture,
            "module_health": self.module_health,
            "ui_context": self.ui_context,
            "memory": {
                "armed": self.memory.armed,
                "current_id": self.memory.current_id,
                "session_count": self.memory.session_count,
                "max_sessions": self.memory.max_sessions,
                "replay_playing": self.memory.replay_playing,
                "replay_session_id": self.memory.replay_session_id,
                "replay_tick": self.memory.replay_tick,
            },
        }
        if self.views:
            payload["views"] = self.views
        if self.deep_research:
            payload["deep_research"] = normalize_deep_research_payload(self.deep_research)
        return payload


def normalize_deep_research_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize deep-research metadata without dropping unknown forward fields."""
    source = payload if isinstance(payload, dict) else {}
    timestamp = float(source.get("timestamp", 0.0))
    normalized = {
        "version": int(source.get("version", 0)),
        "timestamp": timestamp,
        "produced_at": float(source.get("produced_at", timestamp)),
        "source_snapshot_version": int(source.get("source_snapshot_version", 0)),
        "source_snapshot_timestamp": float(source.get("source_snapshot_timestamp", 0.0)),
        "source_tick": int(source.get("source_tick", 0)),
        "late_policy": str(source.get("late_policy", "drop")),
        "stale": bool(source.get("stale", False)),
        "lag_ms": float(source.get("lag_ms", 0.0)),
        "applied": bool(source.get("applied", False)),
        "dropped": bool(source.get("dropped", False)),
        "drop_reason": str(source.get("drop_reason", "")),
        "result": deepcopy(source.get("result", {})),
    }
    for key, value in source.items():
        if key not in normalized:
            normalized[key] = deepcopy(value)
    return normalized


def build_snapshot(
    *,
    timestamp: float,
    tick: int,
    bar: int,
    running: bool,
    bpm: float,
    clock_interval_ms: float = 0.0,
    jitter_rms: float = 0.0,
    clock_jitter_rms: float = 0.0,
    clock_jitter_p95: float = 0.0,
    clock_drift_ppm: float = 0.0,
    microtiming_bins: dict[str, Any] | None = None,
    microtiming_window_events: int = 0,
    microtiming_window_bars: float = 0.0,
    meter_estimate: str = "4/4",
    confidence: float = 0.0,
    active_notes: dict[int, set[int]] | None = None,
    module_outputs: dict[str, Any] | None = None,
    views: dict[str, Any] | None = None,
    status_text: str = "",
    diagnostics: dict[str, Any] | None = None,
    retrospective_capture: dict[str, Any] | None = None,
    module_health: dict[str, Any] | None = None,
    ui_context: dict[str, Any] | None = None,
    deep_research: dict[str, Any] | None = None,
    memory: dict[str, Any] | None = None,
) -> StateSnapshot:
    active_notes = active_notes or {}
    normalized = {ch: sorted(notes) for ch, notes in active_notes.items()}
    channels = [
        ChannelSnapshot(channel=ch, active_notes=notes)
        for ch, notes in sorted(normalized.items(), key=lambda item: item[0])
    ]
    transport_quality = {
        "clock_jitter_rms": float(clock_jitter_rms),
        "clock_jitter_p95": float(clock_jitter_p95),
        "clock_drift_ppm": float(clock_drift_ppm),
    }
    transport_microtiming = {
        "bins": deepcopy(microtiming_bins) if isinstance(microtiming_bins, dict) else {},
        "window_events": int(microtiming_window_events),
        "window_bars": float(microtiming_window_bars),
    }
    normalized_retrospective_capture = {
        "buffer_bars": 0,
        "events_buffered": 0,
        "armed": False,
        "last_commit_path": "",
    }
    if isinstance(retrospective_capture, dict):
        normalized_retrospective_capture.update(retrospective_capture)

    normalized_module_health = {
        "status": "unknown",
        "updated_at": 0.0,
        "modules": {},
    }
    if isinstance(module_health, dict):
        normalized_module_health.update(module_health)

    normalized_memory = {
        "armed": False,
        "current_id": "",
        "session_count": 0,
        "max_sessions": 0,
        "replay_playing": False,
        "replay_session_id": "",
        "replay_tick": 0,
    }
    if isinstance(memory, dict):
        normalized_memory.update(memory)

    return StateSnapshot(
        schema_version=SCHEMA_VERSION,
        timestamp=timestamp,
        transport=TransportSnapshot(
            tick=tick,
            bar=bar,
            running=running,
            bpm=bpm,
            clock_interval_ms=clock_interval_ms,
            jitter_rms=jitter_rms,
            quality=transport_quality,
            microtiming=transport_microtiming,
            meter_estimate=meter_estimate,
            confidence=confidence,
        ),
        channels=channels,
        active_notes=normalized,
        module_outputs=module_outputs or {},
        views=views or None,
        status_text=status_text,
        diagnostics=diagnostics or {},
        retrospective_capture=normalized_retrospective_capture,
        module_health=normalized_module_health,
        ui_context=ui_context or {},
        deep_research=normalize_deep_research_payload(deep_research),
        memory=MemorySnapshot(
            armed=bool(normalized_memory.get("armed", False)),
            current_id=str(normalized_memory.get("current_id", "")),
            session_count=max(0, int(normalized_memory.get("session_count", 0))),
            max_sessions=max(0, int(normalized_memory.get("max_sessions", 0))),
            replay_playing=bool(normalized_memory.get("replay_playing", False)),
            replay_session_id=str(normalized_memory.get("replay_session_id", "")),
            replay_tick=max(0, int(normalized_memory.get("replay_tick", 0))),
        ),
    )
