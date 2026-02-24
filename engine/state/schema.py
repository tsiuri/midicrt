from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 1


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
    status_text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
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
        }


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
    status_text: str = "",
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
        status_text=status_text,
    )
