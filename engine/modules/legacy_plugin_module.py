from __future__ import annotations

from typing import Any

class LegacyPluginModule:
    """Compatibility adapter from plugin-style modules to EngineModule."""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self.name = getattr(plugin, "__name__", plugin.__class__.__name__)
        self._last_outputs: dict[str, Any] = {}
        self._midi_handler = getattr(plugin, "handle", None)

    def on_event(self, event: dict[str, Any]) -> None:
        msg = event.get("raw")
        if hasattr(self.plugin, "on_event"):
            try:
                self.plugin.on_event(event)
            except Exception:
                pass

        if msg is not None and event.get("kind") in (
            "sysex",
            "note_on",
            "note_off",
            "control_change",
            "program_change",
        ) and callable(self._midi_handler):
            try:
                self._midi_handler(msg)
            except Exception:
                pass

    def on_clock(self, snapshot: dict[str, Any]) -> None:
        if hasattr(self.plugin, "on_tick"):
            try:
                self.plugin.on_tick(snapshot)
            except Exception:
                pass

    def get_outputs(self) -> dict[str, Any]:
        outputs: dict[str, Any] = {}
        getter = getattr(self.plugin, "get_state", None)
        if callable(getter):
            try:
                state = getter()
                if state is not None:
                    outputs["state"] = state
            except Exception:
                pass

        for getter_name, key in (("get_timesig", "timesig"), ("get_timesig_exp", "timesig_exp")):
            getter = getattr(self.plugin, getter_name, None)
            if callable(getter):
                try:
                    value = getter()
                    if value is not None:
                        outputs[key] = value
                except Exception:
                    pass

        self._last_outputs = outputs
        return dict(self._last_outputs)
