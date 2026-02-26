from __future__ import annotations

import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True)
class ResearchContract:
    """Frozen handoff contract consumed by deep-research logic."""

    schema_version: int
    snapshot_timestamp: float
    event_kind: str
    transport: MappingProxyType
    active_notes: MappingProxyType
    module_outputs: MappingProxyType


@dataclass(frozen=True)
class IPCFreshnessMeta:
    """Freshness metadata attached to IPC payloads for consumers."""

    source_snapshot_version: int
    source_snapshot_timestamp: float
    handed_off_monotonic: float
    emitted_monotonic: float
    age_ms: float
    stale: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_snapshot_version": self.source_snapshot_version,
            "source_snapshot_timestamp": self.source_snapshot_timestamp,
            "handed_off_monotonic": self.handed_off_monotonic,
            "emitted_monotonic": self.emitted_monotonic,
            "age_ms": round(self.age_ms, 3),
            "stale": self.stale,
        }


class ResearchCadenceScheduler:
    """Minimal cadence scheduler for Track A orchestration."""

    def __init__(self, cadence_hz: float = 2.0) -> None:
        self.cadence_hz = max(0.1, float(cadence_hz))
        self._next_due_ts = 0.0

    def should_enqueue(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else float(now)
        if now < self._next_due_ts:
            return False
        self._next_due_ts = now + (1.0 / self.cadence_hz)
        return True


def freeze_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        frozen = {str(k): freeze_payload(v) for k, v in payload.items()}
        return MappingProxyType(frozen)
    if isinstance(payload, list):
        return tuple(freeze_payload(v) for v in payload)
    if isinstance(payload, set):
        return tuple(sorted(freeze_payload(v) for v in payload))
    if isinstance(payload, tuple):
        return tuple(freeze_payload(v) for v in payload)
    return payload


def thaw_payload(payload: Any) -> Any:
    if isinstance(payload, MappingProxyType):
        return {k: thaw_payload(v) for k, v in payload.items()}
    if isinstance(payload, dict):
        return {k: thaw_payload(v) for k, v in payload.items()}
    if isinstance(payload, tuple):
        return [thaw_payload(v) for v in payload]
    return payload


def build_contract(snapshot: dict[str, Any], event: dict[str, Any]) -> ResearchContract:
    schema = snapshot.get("schema", snapshot) if isinstance(snapshot, dict) else {}
    transport = schema.get("transport", {}) if isinstance(schema, dict) else {}
    return ResearchContract(
        schema_version=int(schema.get("schema_version", 0)),
        snapshot_timestamp=float(schema.get("timestamp", 0.0)),
        event_kind=str(event.get("kind", "")),
        transport=freeze_payload(transport),
        active_notes=freeze_payload(schema.get("active_notes", {})),
        module_outputs=freeze_payload(schema.get("module_outputs", {})),
    )


def freshness_meta(
    *,
    source_snapshot_version: int,
    source_snapshot_timestamp: float,
    handed_off_monotonic: float,
    emitted_monotonic: float | None = None,
    stale_after_ms: float = 150.0,
) -> IPCFreshnessMeta:
    emitted = time.monotonic() if emitted_monotonic is None else float(emitted_monotonic)
    age_ms = max(0.0, (emitted - handed_off_monotonic) * 1000.0)
    stale = age_ms > max(1.0, float(stale_after_ms))
    return IPCFreshnessMeta(
        source_snapshot_version=int(source_snapshot_version),
        source_snapshot_timestamp=float(source_snapshot_timestamp),
        handed_off_monotonic=float(handed_off_monotonic),
        emitted_monotonic=emitted,
        age_ms=age_ms,
        stale=stale,
    )
