import json
import pathlib
import sys
import threading
import time
import types
import unittest
from unittest import mock

import typing

if not hasattr(typing, "NotRequired"):
    try:
        from typing_extensions import NotRequired as _NotRequired

        typing.NotRequired = _NotRequired
    except Exception:  # pragma: no cover
        typing.NotRequired = object

try:
    import mido
except ModuleNotFoundError:  # pragma: no cover - local fallback for offline env
    mido = types.ModuleType("mido")

    class _Message:
        def __init__(self, mtype, **kwargs):
            self.type = mtype
            for key, value in kwargs.items():
                setattr(self, key, value)

        @staticmethod
        def from_bytes(data):
            status = int(data[0])
            channel = status & 0x0F
            kind = "note_on" if (status & 0xF0) == 0x90 else "note_off"
            return _Message(kind, channel=channel, note=int(data[1]), velocity=int(data[2]))

    mido.Message = _Message
    sys.modules["mido"] = mido

from engine.core import MidiEngine
from engine.state.schema import normalize_deep_research_payload
from ui.client import normalize_snapshot


REPLAY_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "capture_replay.json"
CONTRACT_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "deep_research_contract_cases.json"


class _UiClockProbe:
    name = "ui_probe"

    def __init__(self):
        self.clocks = 0

    def on_event(self, _event):
        return None

    def on_clock(self, _snapshot):
        self.clocks += 1

    def get_outputs(self):
        return {"clocks": self.clocks}


class _SlowDeepResearchProbe:
    name = "deepresearch"

    def __init__(self, sleep_s=0.02):
        self.sleep_s = sleep_s
        self.runs = 0

    def on_event(self, _event):
        time.sleep(self.sleep_s)

    def on_clock(self, _snapshot):
        return None

    def get_outputs(self):
        self.runs += 1
        return {"runs": self.runs}


class _DeterministicDeepResearchProbe:
    name = "deepresearch"

    def __init__(self):
        self.last_event = {}
        self.events = 0

    def on_event(self, event):
        self.last_event = dict(event)
        self.events += 1

    def on_clock(self, _snapshot):
        return None

    def get_outputs(self):
        return {
            "events": self.events,
            "event_kind": self.last_event.get("kind", ""),
            "signature": f"{self.last_event.get('kind', '')}:{self.last_event.get('note', -1)}:{self.last_event.get('velocity', -1)}",
        }


class _BlockingDeepResearchProbe:
    name = "deepresearch"

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def on_event(self, _event):
        self.started.set()
        self.release.wait(timeout=1.0)

    def on_clock(self, _snapshot):
        return None

    def get_outputs(self):
        return {"status": "ready"}


def _msg_from_bytes(data):
    from_bytes = getattr(mido.Message, "from_bytes", None)
    if callable(from_bytes):
        return from_bytes(data)
    status = int(data[0])
    channel = status & 0x0F
    kind = "note_on" if (status & 0xF0) == 0x90 else "note_off"
    return mido.Message(kind, channel=channel, note=int(data[1]), velocity=int(data[2]))


class DeepResearchReplayContractsTest(unittest.TestCase):
    def test_scheduler_overrun_in_deep_research_lane_does_not_starve_ui_clock(self):
        ui = _UiClockProbe()
        deep = _SlowDeepResearchProbe(sleep_s=0.03)
        engine = MidiEngine(
            modules=[ui, deep],
            module_policies={"ui_probe": {"policy": "clock_driven"}},
            deep_research_settings={"enabled": True, "cadence_hz": 100000.0, "max_runtime_ms": 1.0, "queue_size": 1},
        )

        with mock.patch("engine.core.time.time", return_value=100.0):
            for _ in range(4):
                engine.ingest(mido.Message("clock"))

        engine._deep_research_q.join()
        snap = engine.get_snapshot()["schema"]
        metrics = snap["diagnostics"]["modules"]["deep_research"]["metrics"]

        self.assertEqual(ui.clocks, 4)
        self.assertGreaterEqual(metrics["overruns"], 1)
        self.assertGreaterEqual(metrics["dropped"], 1)

    def test_deterministic_outputs_for_same_input_snapshot_stream(self):
        payload = json.loads(REPLAY_FIXTURE.read_text())

        def run_once():
            engine = MidiEngine(modules=[_DeterministicDeepResearchProbe()], deep_research_settings={"enabled": True, "cadence_hz": 5000.0})
            outputs = []
            for event in payload["capture_events"]:
                msg = _msg_from_bytes(event["bytes"])
                with mock.patch("engine.core.time.time", return_value=event["timestamp"]):
                    engine.ingest(msg)
                engine._deep_research_q.join()
                outputs.append(engine.get_snapshot()["schema"].get("deep_research", {}).get("result", {}))
            return outputs

        self.assertEqual(run_once(), run_once())

    def test_stale_result_policy_fixture_drop_or_apply_next(self):
        cases = [
            {"late_policy": "drop", "expect_dropped": True, "expect_applied": False},
            {"late_policy": "apply_next", "expect_dropped": False, "expect_applied": True},
        ]

        for case in cases:
            with self.subTest(case=case["late_policy"]):
                probe = _BlockingDeepResearchProbe()
                engine = MidiEngine(
                    modules=[probe],
                    deep_research_settings={"enabled": True, "cadence_hz": 100.0, "late_policy": case["late_policy"]},
                )

                with mock.patch("engine.core.time.time", return_value=300.0):
                    engine.ingest(mido.Message("note_on", note=60, velocity=100, channel=0))

                self.assertTrue(probe.started.wait(timeout=1.0))
                with engine._lock:
                    engine._deep_research_latest_snapshot_version += 1
                probe.release.set()
                engine._deep_research_q.join()

                deep = engine.get_snapshot()["schema"]["deep_research"]
                self.assertTrue(deep["stale"])
                self.assertEqual(deep["dropped"], case["expect_dropped"])
                self.assertEqual(deep["applied"], case["expect_applied"])

    def test_ipc_payload_compatibility_fixture_for_deep_research_absent_and_present(self):
        fixture = json.loads(CONTRACT_FIXTURE.read_text())
        for case in fixture["ipc_compat"]:
            with self.subTest(case=case["name"]):
                normalized = normalize_snapshot(case["input"])
                has_deep = isinstance(normalized.get("deep_research"), dict)
                self.assertEqual(has_deep, case["expect_has_deep_research"])
                if has_deep:
                    self.assertEqual(normalized["deep_research"]["version"], case["expected_version"])

    def test_schema_contract_validation_for_deep_research_fields(self):
        fixture = json.loads(CONTRACT_FIXTURE.read_text())
        for case in fixture["schema_contract"]:
            with self.subTest(case=case["name"]):
                normalized = normalize_deep_research_payload(case["input"])
                for key, value in case["expected"].items():
                    self.assertEqual(normalized[key], value)


if __name__ == "__main__":
    unittest.main()
