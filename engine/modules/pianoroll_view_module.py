from __future__ import annotations

from typing import Any, Callable


class PianoRollViewModule:
    """Engine module that publishes normalized piano-roll view payloads."""

    name = "pianoroll_view"

    def __init__(self, payload_getter: Callable[[], dict[str, Any] | None]) -> None:
        self._payload_getter = payload_getter
        self._cached: dict[str, Any] | None = None

    def on_event(self, event: dict[str, Any]) -> None:
        # payload is computed lazily in get_outputs()
        return

    def on_clock(self, snapshot: dict[str, Any]) -> None:
        # no periodic work required; payload getter may consult live state
        return

    def get_outputs(self) -> dict[str, Any]:
        try:
            payload = self._payload_getter()
        except Exception:
            payload = None
        if isinstance(payload, dict) and payload:
            self._cached = payload
        views: dict[str, Any] = {}
        if isinstance(self._cached, dict) and self._cached:
            views["8"] = self._cached
            views["pianoroll"] = self._cached
        return {"views": views} if views else {}
