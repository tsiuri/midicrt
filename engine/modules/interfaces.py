from __future__ import annotations

from typing import Any, Protocol


class EngineModule(Protocol):
    """Explicit engine module lifecycle.

    Modules consume normalized events, run periodic work on MIDI clock ticks,
    and expose computed outputs for snapshots.
    """

    name: str

    def on_event(self, event: dict[str, Any]) -> None: ...

    def on_clock(self, snapshot: dict[str, Any]) -> None: ...

    def get_outputs(self) -> dict[str, Any]: ...
