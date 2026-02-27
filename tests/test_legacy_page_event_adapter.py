import unittest

from engine.adapters import LegacyPageEventAdapter


class _Page:
    def __init__(self, *, background=False):
        self.BACKGROUND = background
        self.handled = []
        self.ticks = []

    def handle(self, msg):
        self.handled.append(msg)

    def on_tick(self, state):
        self.ticks.append(dict(state))


class LegacyPageEventAdapterTest(unittest.TestCase):
    def test_clock_routes_on_tick_to_background_pages_except_current(self):
        fg = _Page(background=False)
        bg1 = _Page(background=True)
        bg2 = _Page(background=True)
        calls = {"plugin_state": 0}

        def plugin_state_provider():
            calls["plugin_state"] += 1
            return {"tick": 123}

        adapter = LegacyPageEventAdapter(
            pages_provider=lambda: {1: fg, 2: bg1, 3: bg2},
            current_page_provider=lambda: 2,
            plugin_state_provider=plugin_state_provider,
        )

        adapter.route({"kind": "clock"})

        self.assertEqual(calls["plugin_state"], 1)
        self.assertEqual(bg1.ticks, [])
        self.assertEqual(bg2.ticks, [{"tick": 123}])
        self.assertEqual(fg.ticks, [])

    def test_midi_routes_current_and_background_handlers_and_activity(self):
        fg = _Page(background=False)
        bg = _Page(background=True)
        inactive = _Page(background=False)
        msg = object()
        activity = []

        adapter = LegacyPageEventAdapter(
            pages_provider=lambda: {1: fg, 2: bg, 3: inactive},
            current_page_provider=lambda: 1,
            midi_activity_handler=lambda value: activity.append(value),
        )

        adapter.route({"kind": "note_on", "raw": msg})

        self.assertEqual(fg.handled, [msg])
        self.assertEqual(bg.handled, [msg])
        self.assertEqual(inactive.handled, [])
        self.assertEqual(activity, [msg])

    def test_fallback_behavior_handles_disabled_missing_or_bad_providers(self):
        adapter = LegacyPageEventAdapter(enabled=False)
        adapter.route({"kind": "note_on", "raw": object()})

        adapter = LegacyPageEventAdapter(pages_provider=None)
        adapter.route({"kind": "clock"})

        adapter = LegacyPageEventAdapter(
            pages_provider=lambda: {1: _Page(background=True)},
            current_page_provider=lambda: 1,
            plugin_state_provider=lambda: "bad-state",
        )
        adapter.route({"kind": "clock"})

        page = _Page(background=False)
        adapter = LegacyPageEventAdapter(
            pages_provider=lambda: {1: page},
            current_page_provider=lambda: 1,
        )
        adapter.route({"kind": "pitchwheel", "raw": object()})
        self.assertEqual(page.handled, [])


if __name__ == "__main__":
    unittest.main()
