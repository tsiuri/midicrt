import sys
import threading
import time
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


class _SlowDeepResearchProbe(_DeepResearchProbe):
    def __init__(self, sleep_s=0.01):
        super().__init__()
        self.sleep_s = sleep_s

    def on_event(self, event):
        time.sleep(self.sleep_s)
        super().on_event(event)





class _SlowDeepResearchLowPriority(_DeepResearchProbe):
    name = "deepresearch"

    def __init__(self, sleep_s=0.012):
        super().__init__()
        self.sleep_s = sleep_s

    def on_event(self, event):
        time.sleep(self.sleep_s)
        super().on_event(event)

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
            side_effect=[1.000, 1.000, 1.020, 1.021],
        ):
            engine.ingest(mido.Message("clock"))

        snap = engine.get_snapshot()["schema"]
        overloaded = snap["diagnostics"]["scheduler"]["overloaded_modules"]
        self.assertEqual(overloaded, ["probe"])
        self.assertIn("overload:probe", snap["status_text"])

    def test_deep_research_overrun_defers_next_cycle_and_tracks_drops(self):
        mod = _SlowDeepResearchProbe(sleep_s=0.01)
        engine = MidiEngine(
            modules=[mod],
            deep_research_settings={"enabled": True, "cadence_hz": 500.0, "max_runtime_ms": 1.0, "queue_size": 1},
        )

        with mock.patch("engine.core.time.time", return_value=300.0):
            engine.ingest(mido.Message("note_on", note=60, velocity=100, channel=0))
            time.sleep(0.003)
            engine.ingest(mido.Message("note_on", note=61, velocity=100, channel=0))
            engine._deep_research_q.join()

        snap = engine.get_snapshot()
        metrics = snap["schema"]["diagnostics"]["modules"]["deep_research"]["metrics"]
        self.assertGreaterEqual(mod.events, 1)
        self.assertGreaterEqual(metrics["runs"], 1)
        self.assertGreaterEqual(metrics["overruns"], 1)
        self.assertGreaterEqual(metrics["dropped"], 1)
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


    def test_deep_research_degradation_skips_low_priority_cycles_when_budget_exceeded(self):
        mod = _SlowDeepResearchLowPriority(sleep_s=0.008)
        engine = MidiEngine(
            modules=[mod],
            deep_research_settings={
                "enabled": True,
                "cadence_hz": 1000.0,
                "max_runtime_ms": 50.0,
                "modules": ["deepresearch"],
                "budget": {
                    "module_budget_ms": 1.0,
                    "degradation_policy": "skip_low_priority",
                    "degradation_skip_cycles": 2,
                    "degradation_priority_threshold": 80,
                    "module_priorities": {"deepresearch": 10},
                },
            },
        )

        with mock.patch("engine.core.time.time", return_value=600.0):
            for _ in range(4):
                engine.ingest(mido.Message("note_on", note=60, velocity=100, channel=0))
                time.sleep(0.003)

        engine._deep_research_q.join()
        metrics = engine.get_snapshot()["schema"]["diagnostics"]["modules"]["deep_research"]["metrics"]
        mod_metrics = metrics["modules"]["deepresearch"]
        self.assertGreaterEqual(mod_metrics["over_budget_count"], 1)
        self.assertGreaterEqual(mod_metrics["skipped_due_degradation"], 1)
        self.assertGreaterEqual(metrics["degradation_active_cycles"], 0)
        self.assertLess(mod.events, 4)


if __name__ == "__main__":
    unittest.main()
