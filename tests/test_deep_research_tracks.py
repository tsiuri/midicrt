import json
import unittest
from pathlib import Path

from engine.deep_research import (
    DeterministicMockResearchModule,
    ResearchCadenceScheduler,
    build_contract,
    freshness_meta,
    run_research,
)


class DeepResearchTrackSplitTest(unittest.TestCase):
    def test_track_a_scheduler_and_freshness_metadata(self):
        scheduler = ResearchCadenceScheduler(cadence_hz=2.0)
        self.assertTrue(scheduler.should_enqueue(now=1.0))
        self.assertFalse(scheduler.should_enqueue(now=1.2))
        self.assertTrue(scheduler.should_enqueue(now=1.6))

        meta = freshness_meta(
            source_snapshot_version=10,
            source_snapshot_timestamp=200.0,
            handed_off_monotonic=1.0,
            emitted_monotonic=1.25,
            stale_after_ms=100.0,
        ).as_dict()
        self.assertEqual(meta["source_snapshot_version"], 10)
        self.assertTrue(meta["stale"])

    def test_track_a_deterministic_mock_output(self):
        snapshot = {
            "schema": {
                "schema_version": 4,
                "timestamp": 250.0,
                "transport": {"tick": 18, "bar": 1, "running": True, "bpm": 123.0},
                "active_notes": {"0": [60, 64]},
                "module_outputs": {},
            }
        }
        contract = build_contract(snapshot, {"kind": "clock"})
        out = DeterministicMockResearchModule.run(contract)
        self.assertEqual(out["signature"], "v4:clock:18")
        self.assertEqual(out["active_channel_count"], 1)

    def test_track_b_fixture_sequences(self):
        fixture_path = Path("tests/fixtures/deep_research_sequences.json")
        cases = json.loads(fixture_path.read_text())
        for case in cases:
            with self.subTest(case=case["name"]):
                snapshot = {
                    "schema": {
                        "schema_version": case["schema_version"],
                        "timestamp": 123.0,
                        "transport": case["transport"],
                        "active_notes": case["active_notes"],
                        "module_outputs": {},
                    }
                }
                contract = build_contract(snapshot, case["event"])
                result = run_research(contract)
                self.assertEqual(result, case["expected"])


if __name__ == "__main__":
    unittest.main()
