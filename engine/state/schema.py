from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 4


@dataclass
class TransportSnapshot:
    tick: int
    bar: int
    running: bool
    bpm: float
    clock_interval_ms: float = 0.0
    jitter_rms: float = 0.0
    meter_estimate: str = "4/4"
    confidence: float = 0.0


@dataclass
class ChannelSnapshot:
    channel: int
    active_notes: list[int] = field(default_factory=list)


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
    ui_context: dict[str, Any] = field(default_factory=dict)
    deep_research: dict[str, Any] | None = None

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
            "ui_context": self.ui_context,
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
    meter_estimate: str = "4/4",
    confidence: float = 0.0,
    active_notes: dict[int, set[int]] | None = None,
    module_outputs: dict[str, Any] | None = None,
    views: dict[str, Any] | None = None,
    status_text: str = "",
    diagnostics: dict[str, Any] | None = None,
    ui_context: dict[str, Any] | None = None,
    deep_research: dict[str, Any] | None = None,
) -> StateSnapshot:
    active_notes = active_notes or {}
    normalized = {ch: sorted(notes) for ch, notes in active_notes.items()}
    channels = [
        ChannelSnapshot(channel=ch, active_notes=notes)
        for ch, notes in sorted(normalized.items(), key=lambda item: item[0])
    ]
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
            meter_estimate=meter_estimate,
            confidence=confidence,
        ),
        channels=channels,
        active_notes=normalized,
        module_outputs=module_outputs or {},
        views=views or None,
        status_text=status_text,
        diagnostics=diagnostics or {},
        ui_context=ui_context or {},
        deep_research=normalize_deep_research_payload(deep_research),
    )
