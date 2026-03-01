from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class LegacyPageRouter:
    pages_provider: Callable[[], dict[int, Any]] | None = None
    current_page_provider: Callable[[], int] | None = None
    plugin_state_provider: Callable[[], dict[str, Any]] | None = None
    midi_activity_handler: Callable[[Any], None] | None = None
    enabled: bool = True

    _MIDI_BG_KINDS = {
        "note_on",
        "note_off",
        "control_change",
        "program_change",
        "start",
        "continue",
        "stop",
    }
    _bg_tick_hz: float = 24.0
    _bg_tick_last_ts: float = 0.0

    def route(self, event: dict[str, Any]) -> None:
        if not self.enabled or not callable(self.pages_provider):
            return
        kind = str(event.get("kind", ""))
        msg = event.get("raw")
        # Only fetch pages dict when we actually need it.
        if kind != "clock" and (msg is None or kind not in self._MIDI_BG_KINDS):
            return
        if not hasattr(self, "_pages_cache") or self._pages_cache is None:
            self._pages_cache = self.pages_provider()
        pages = self._pages_cache
        if not isinstance(pages, dict) or not pages:
            return

        current_page = int(self.current_page_provider() if callable(self.current_page_provider) else -1)

        if kind == "clock":
            self._maybe_route_background_ticks(pages, current_page)
        if msg is not None and kind in self._MIDI_BG_KINDS:
            self._route_midi_handlers(pages, current_page, msg)
            # Keep BACKGROUND pages responsive even when transport clocks are sparse.
            self._maybe_route_background_ticks(pages, current_page)

    def _maybe_route_background_ticks(self, pages: dict[int, Any], current_page: int) -> None:
        hz = max(1.0, float(self._bg_tick_hz))
        now = time.monotonic()
        if (now - float(self._bg_tick_last_ts)) < (1.0 / hz):
            return
        self._bg_tick_last_ts = now
        self._route_background_ticks(pages, current_page)

    def _route_background_ticks(self, pages: dict[int, Any], current_page: int) -> None:
        plugin_state = None
        for pid, page in pages.items():
            if pid == current_page:
                continue
            if not bool(getattr(page, "BACKGROUND", False)) or not hasattr(page, "on_tick"):
                continue
            try:
                if plugin_state is None:
                    plugin_state = self.plugin_state_provider() if callable(self.plugin_state_provider) else {}
                page.on_tick(plugin_state if isinstance(plugin_state, dict) else {})
            except Exception:
                pass

    def _route_midi_handlers(self, pages: dict[int, Any], current_page: int, msg: Any) -> None:
        page = pages.get(current_page)
        if page is not None and hasattr(page, "handle"):
            try:
                page.handle(msg)
            except Exception:
                pass

        for pid, page in pages.items():
            if pid == current_page:
                continue
            if bool(getattr(page, "BACKGROUND", False)) and hasattr(page, "handle"):
                try:
                    page.handle(msg)
                except Exception:
                    pass

        if callable(self.midi_activity_handler):
            try:
                self.midi_activity_handler(msg)
            except Exception:
                pass
