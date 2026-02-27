from __future__ import annotations

import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


RESEARCH_CONTRACT_MAJOR_VERSION = 1
RESEARCH_CONTRACT_MINOR_VERSION = 0


@dataclass(frozen=True)
class ResearchContract:
    """Frozen handoff contract consumed by deep-research logic."""

    contract_version: str
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


def current_contract_version() -> str:
    return f"{RESEARCH_CONTRACT_MAJOR_VERSION}.{RESEARCH_CONTRACT_MINOR_VERSION}"


def contract_versions_compatible(expected_version: str, actual_version: str) -> bool:
    """Return True if actual contract version is compatible with expected.

    Compatibility is major-version locked and minor-version forward compatible
    for additive changes.
    """

    def _parse(version: str) -> tuple[int, int]:
        major_text, sep, minor_text = str(version).partition(".")
        if not sep:
            raise ValueError("version must be '<major>.<minor>'")
        return int(major_text), int(minor_text)

    expected_major, expected_minor = _parse(expected_version)
    actual_major, actual_minor = _parse(actual_version)
    if expected_major != actual_major:
        return False
    return actual_minor >= expected_minor


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


def resolve_feature_flags(settings: dict[str, Any] | None) -> dict[str, bool]:
    """Resolve deep-research feature flags with safe defaults."""
    cfg = settings if isinstance(settings, dict) else {}
    ff = cfg.get("feature_flags", {}) if isinstance(cfg.get("feature_flags"), dict) else {}

    def _flag(name: str, default: bool) -> bool:
        return bool(ff.get(name, default))

    return {
        # Track-A executor gates.
        "enable_module_execution": _flag("enable_module_execution", True),
        "enable_payload_result": _flag("enable_payload_result", True),
        "enable_payload_metadata": _flag("enable_payload_metadata", True),
        # Contract input surface gates.
        "enable_contract_module_outputs": _flag("enable_contract_module_outputs", True),
        "enable_contract_views": _flag("enable_contract_views", True),
        # UI snapshot surface gates.
        "enable_ui_surface_module_outputs": _flag("enable_ui_surface_module_outputs", True),
        "enable_ui_surface_views": _flag("enable_ui_surface_views", True),
        "enable_ui_surface_deep_research": _flag("enable_ui_surface_deep_research", True),
    }


def filter_contract_module_outputs(snapshot: dict[str, Any], *, include_module_outputs: bool, include_views: bool) -> dict[str, Any]:
    schema = snapshot.get("schema", snapshot) if isinstance(snapshot, dict) else {}
    if not isinstance(schema, dict):
        return {}

    outputs = schema.get("module_outputs", {})
    if not include_module_outputs:
        return {}
    if include_views:
        return outputs if isinstance(outputs, dict) else {}

    # Exclude view-like payloads from module outputs when disabled.
    filtered: dict[str, Any] = {}
    if not isinstance(outputs, dict):
        return filtered
    for name, payload in outputs.items():
        if not isinstance(payload, dict):
            filtered[str(name)] = payload
            continue
        local = dict(payload)
        local.pop("views", None)
        filtered[str(name)] = local
    return filtered


def build_contract(snapshot: dict[str, Any], event: dict[str, Any]) -> ResearchContract:
    schema = snapshot.get("schema", snapshot) if isinstance(snapshot, dict) else {}
    transport = schema.get("transport", {}) if isinstance(schema, dict) else {}
    research_settings = schema.get("deep_research", {}) if isinstance(schema.get("deep_research"), dict) else {}
    flags = resolve_feature_flags(research_settings)
    return ResearchContract(
        contract_version=current_contract_version(),
        schema_version=int(schema.get("schema_version", 0)),
        snapshot_timestamp=float(schema.get("timestamp", 0.0)),
        event_kind=str(event.get("kind", "")),
        transport=freeze_payload(transport),
        active_notes=freeze_payload(schema.get("active_notes", {})),
        module_outputs=freeze_payload(
            filter_contract_module_outputs(
                snapshot,
                include_module_outputs=flags["enable_contract_module_outputs"],
                include_views=flags["enable_contract_views"],
            )
        ),
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
