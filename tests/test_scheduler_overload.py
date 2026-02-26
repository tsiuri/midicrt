import sys
import threading
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


class _DeepResearchProbe(_ProbeModule):
    name = "deepresearch"




class _BlockingDeepResearchProbe(_DeepResearchProbe):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def on_event(self, event):
        self.started.set()
        self.release.wait(timeout=1.0)
        super().on_event(event)

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

    def test_deep_research_overrun_defers_next_cycle_and_tracks_drops(self):
        mod = _DeepResearchProbe()
        engine = MidiEngine(
            modules=[mod],
            deep_research_settings={"enabled": True, "cadence_hz": 10.0, "max_runtime_ms": 5.0, "queue_size": 1},
        )

        mono = [1.000, 1.000, 1.020, 1.030, 1.030] * 4
        with mock.patch("engine.core.time.time", return_value=300.0), mock.patch(
            "engine.scheduler.time.monotonic",
            side_effect=mono,
        ), mock.patch("engine.core.time.monotonic", side_effect=mono):
            engine.ingest(mido.Message("note_on", note=60, velocity=100, channel=0))
            engine.ingest(mido.Message("note_on", note=61, velocity=100, channel=0))
            engine._deep_research_q.join()

        snap = engine.get_snapshot()
        metrics = snap["schema"]["diagnostics"]["modules"]["deep_research"]["metrics"]
        self.assertEqual(mod.events, 1)
        self.assertEqual(metrics["runs"], 1)
        self.assertEqual(metrics["overruns"], 1)
        self.assertEqual(metrics["dropped"], 1)
        self.assertIn(metrics["deferred"], [0, 1])
        self.assertEqual(metrics["last_drop_reason"], "runtime_over_budget")
        self.assertIn("metrics", snap["modules"]["deep_research"])

    def test_late_worker_result_is_dropped_with_default_policy(self):
        mod = _BlockingDeepResearchProbe()
        engine = MidiEngine(modules=[mod], deep_research_settings={"enabled": True, "cadence_hz": 100.0})

        with mock.patch("engine.core.time.time", return_value=400.0):
            engine.ingest(mido.Message("note_on", note=60, velocity=100, channel=0))

        self.assertTrue(mod.started.wait(timeout=1.0))
        with engine._lock:
            engine._deep_research_latest_snapshot_version += 1
        mod.release.set()
        engine._deep_research_q.join()

        deep = engine.get_snapshot()["schema"]["deep_research"]
        self.assertTrue(deep["stale"])
        self.assertTrue(deep["dropped"])
        self.assertEqual(deep["drop_reason"], "late_result")

    def test_late_worker_result_can_apply_next_when_configured(self):
        mod = _BlockingDeepResearchProbe()
        engine = MidiEngine(
            modules=[mod],
            deep_research_settings={"enabled": True, "cadence_hz": 100.0, "late_policy": "apply_next"},
        )

        with mock.patch("engine.core.time.time", return_value=500.0):
            engine.ingest(mido.Message("note_on", note=60, velocity=100, channel=0))

        self.assertTrue(mod.started.wait(timeout=1.0))
        with engine._lock:
            engine._deep_research_latest_snapshot_version += 1
        mod.release.set()
        engine._deep_research_q.join()

        deep = engine.get_snapshot()["schema"]["deep_research"]
        self.assertTrue(deep["stale"])
        self.assertFalse(deep["dropped"])
        self.assertTrue(deep["applied"])
        self.assertEqual(deep["late_policy"], "apply_next")



if __name__ == "__main__":
    unittest.main()
