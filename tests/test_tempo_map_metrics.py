import json
import math
import pathlib
import unittest

from engine.state.tempo_map import TempoMap


FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "tempo_map_replay.json"


class TempoMapMetricsTest(unittest.TestCase):
    def test_bpm_and_jitter_metrics(self):
        tm = TempoMap(interval_window=8)
        tm.handle("start", 0.0)

        # 120 BPM clock: 24 clocks/beat => one clock every 1/48 second.
        intervals = [1.0 / 48.0, 1.0 / 48.0, 1.0 / 48.0, 1.2 / 48.0, 0.8 / 48.0]
        ts = 1.0
        for dt in intervals:
            ts += dt
            tm.handle("clock", ts)

        snap = tm.snapshot()
        self.assertGreater(snap.bpm, 100.0)
        self.assertLess(snap.bpm, 140.0)
        self.assertGreater(snap.jitter_rms, 0.0)
        self.assertTrue(math.isfinite(snap.clock_interval_ms))

    def test_meter_candidate_selection_prefers_weighted_labels(self):
        tm = TempoMap()
        tm.handle("start", 0.0)
        candidates = [
            {"labels": ["7/8", "4/4"], "confidence": 0.9},
            {"labels": ["4/4"], "confidence": 0.8},
        ]
        tm.handle("clock", 1.0, meter_candidates=candidates)
        snap = tm.snapshot()

        self.assertEqual(snap.meter_estimate, "4/4")
        self.assertAlmostEqual(snap.confidence, 0.9, places=6)

    def test_fixture_replay_is_stable(self):
        payload = json.loads(FIXTURE.read_text())
        tm = TempoMap(interval_window=payload["interval_window"])

        for ev in payload["events"]:
            tm.handle(ev["kind"], ev["timestamp"], meter_candidates=ev.get("meter_candidates"))

        snap = tm.snapshot()
        expected = payload["expected"]
        self.assertEqual(snap.tick_counter, expected["tick_counter"])
        self.assertEqual(snap.bar_counter, expected["bar_counter"])
        self.assertEqual(snap.meter_estimate, expected["meter_estimate"])
        self.assertAlmostEqual(snap.bpm, expected["bpm"], places=6)
        self.assertAlmostEqual(snap.jitter_rms, expected["jitter_rms"], places=6)


if __name__ == "__main__":
    unittest.main()
