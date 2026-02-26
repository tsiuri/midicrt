from __future__ import annotations

from typing import Any, Literal, NotRequired, Protocol, TypedDict, runtime_checkable


class EngineModule(Protocol):
    """Explicit engine module lifecycle.

    Modules consume normalized events, run periodic work on MIDI clock ticks,
    and expose computed outputs for snapshots.
    """

    name: str

    def on_event(self, event: dict[str, Any]) -> None: ...

    def on_clock(self, snapshot: dict[str, Any]) -> None: ...

    def get_outputs(self) -> dict[str, Any]: ...


@runtime_checkable
class LegacyPageModule(Protocol):
    """Page module interface for legacy event hooks."""

    PAGE_ID: int
    BACKGROUND: bool

    def handle(self, msg: Any) -> None: ...

    def on_tick(self, state: dict[str, Any]) -> None: ...


@runtime_checkable
class ScreenSaverModule(Protocol):
    """Screensaver module contract used by keyboard/midi activity routing."""

    def is_active(self) -> bool: ...

    def deactivate(self) -> None: ...


@runtime_checkable
class UserActivityModule(Protocol):
    """Module contract for user-activity notifications."""

    def notify_keypress(self) -> None: ...


@runtime_checkable
class MidiMessageHandler(Protocol):
    """Simple explicit protocol for modules that consume raw MIDI messages."""

    def handle(self, msg: Any) -> None: ...


class EngineEventRouter(Protocol):
    """Optional engine-side event router for compatibility shims."""

    def route_event(self, event: dict[str, Any], snapshot: dict[str, Any]) -> None: ...


CadencePolicy = Literal["every_tick", "throttled_hz", "event_triggered"]
SkipPolicy = Literal["skip_cycle", "degrade", "run_anyway"]
DeepResearchStatus = Literal["ok", "skipped", "error", "disabled"]


class DeepResearchTransportInput(TypedDict):
    """Required transport keys sourced from ``engine/state/schema.py`` snapshots."""

    tick: int
    bar: int
    running: bool
    bpm: float


class DeepResearchSnapshotInput(TypedDict):
    """Minimal required input payload for the DeepResearch module."""

    schema_version: int
    timestamp: float
    transport: DeepResearchTransportInput
    active_notes: dict[str, list[int]]
    module_outputs: dict[str, Any]
    diagnostics: dict[str, Any]
    ui_context: dict[str, Any]


class DeepResearchCadenceSpec(TypedDict):
    """When this module should execute."""

    policy: CadencePolicy
    hz: NotRequired[float]
    trigger_events: NotRequired[list[str]]


class DeepResearchBudgetSpec(TypedDict):
    """Per-cycle compute budget and timeout behavior."""

    max_compute_ms: float
    timeout_ms: float
    on_budget_exceeded: SkipPolicy


class DeepResearchOutputPayload(TypedDict):
    """Output envelope published under ``modules.deepresearch`` and optional views."""

    status: DeepResearchStatus
    schema_version_seen: int
    generated_at: float
    stale: bool
    retained_last_good: bool
    summary: str
    findings: list[dict[str, Any]]
    error: NotRequired[str]
    meta: NotRequired[dict[str, Any]]


class DeepResearchModuleOutputs(TypedDict):
    """Top-level output keys for module snapshots."""

    modules: dict[str, DeepResearchOutputPayload]
    views: NotRequired[dict[str, Any]]


class DeepResearchFailureState(TypedDict):
    """Failure-mode reporting while retaining last-known-good output."""

    status: DeepResearchStatus
    error: str
    retained_last_good: bool
    stale: bool


class DeepResearchConfig(TypedDict):
    """``config/settings.json`` contract for deep-research execution."""

    enabled: bool
    cadence: DeepResearchCadenceSpec
    budget: DeepResearchBudgetSpec
    feature_flags: dict[str, bool]


@runtime_checkable
class DeepResearchModule(Protocol):
    """Contract for expensive, policy-driven deep analysis modules.

    The module consumes a normalized snapshot subset, applies cadence and budget
    policy, and returns output under ``modules.deepresearch`` (plus optional
    view material under ``views.deepresearch``).
    """

    module_key: Literal["deepresearch"]

    def should_run(
        self,
        snapshot: DeepResearchSnapshotInput,
        *,
        event_type: str | None = None,
    ) -> bool: ...

    def run_cycle(self, snapshot: DeepResearchSnapshotInput) -> DeepResearchOutputPayload: ...

    def on_failure(
        self,
        err: Exception,
        snapshot: DeepResearchSnapshotInput,
    ) -> DeepResearchFailureState: ...

    def get_config(self) -> DeepResearchConfig: ...
