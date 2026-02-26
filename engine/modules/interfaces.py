from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


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
