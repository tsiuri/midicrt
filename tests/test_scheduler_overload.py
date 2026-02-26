import sys
import types
import unittest
from unittest import mock

try:
    import mido
except ModuleNotFoundError:  # pragma: no cover - local fallback for offline env
    mido = types.ModuleType("mido")

    class _Message:
        def __init__(self, mtype, **kwargs):
            self.type = mtype
            for key, value in kwargs.items():
                setattr(self, key, value)

    mido.Message = _Message
    sys.modules["mido"] = mido

from engine.core import MidiEngine


class _ProbeModule:
    name = "probe"

    def __init__(self):
        self.events = 0
        self.clocks = 0

    def on_event(self, _event):
        self.events += 1

    def on_clock(self, _snapshot):
        self.clocks += 1

    def get_outputs(self):
        return {"events": self.events, "clocks": self.clocks}


class SchedulerOverloadTest(unittest.TestCase):
    def test_clock_event_skips_event_driven_module_and_tracks_skip_diag(self):
        mod = _ProbeModule()
        engine = MidiEngine(modules=[mod])

        with mock.patch("engine.core.time.time", return_value=100.0):
            engine.ingest(mido.Message("clock"))

        snap = engine.get_snapshot()["schema"]
        diag = snap["diagnostics"]
        self.assertEqual(mod.events, 0)
        self.assertEqual(mod.clocks, 0)
        self.assertEqual(diag["modules"]["probe"]["runs"], 0)
        self.assertGreaterEqual(diag["modules"]["probe"]["skips"], 1)
        self.assertEqual(diag["scheduler"]["overloaded_modules"], [])

    def test_overloaded_modules_lists_slow_module(self):
        mod = _ProbeModule()
        engine = MidiEngine(
            modules=[mod],
            overload_cost_ms=5.0,
            module_policies={"probe": {"policy": "clock_driven"}},
        )

        with mock.patch("engine.core.time.time", return_value=200.0), mock.patch(
            "engine.scheduler.time.monotonic",
            side_effect=[1.000, 1.000, 1.020],
        ):
            engine.ingest(mido.Message("clock"))

        snap = engine.get_snapshot()["schema"]
        overloaded = snap["diagnostics"]["scheduler"]["overloaded_modules"]
        self.assertEqual(overloaded, ["probe"])
        self.assertIn("overload:probe", snap["status_text"])


if __name__ == "__main__":
    unittest.main()
