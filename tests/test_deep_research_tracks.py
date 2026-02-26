import re
import unittest

from engine.deep_research import (
    DeterministicMockResearchModule,
    ResearchCadenceScheduler,
    ResearchContract,
    build_contract,
    current_contract_version,
    freeze_payload,
    freshness_meta,
    run_research,
)

from tests.deep_research_fixture_loader import (
    REQUIRED_KEYS,
    discover_deep_research_sequence_fixture_paths,
    load_deep_research_sequence_fixture,
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
        for fixture_path in discover_deep_research_sequence_fixture_paths():
            case = load_deep_research_sequence_fixture(fixture_path)
            with self.subTest(case=case["name"], fixture=fixture_path.name):
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

    def test_track_b_fixture_files_have_required_keys_and_unique_names(self):
        names = set()
        lane_topic_filename_re = re.compile(r"^(logic_density|transport_tick)_[a-z0-9_]+\.json$")
        for fixture_path in discover_deep_research_sequence_fixture_paths():
            with self.subTest(fixture=fixture_path.name):
                self.assertRegex(fixture_path.name, lane_topic_filename_re)
                case = load_deep_research_sequence_fixture(fixture_path)
                self.assertTrue(REQUIRED_KEYS.issubset(case.keys()))
                self.assertNotIn(case["name"], names)
                names.add(case["name"])

    def test_research_contract_current_version_success(self):
        contract = build_contract(
            {
                "schema": {
                    "schema_version": 7,
                    "timestamp": 300.0,
                    "transport": {"tick": 5, "bar": 1, "running": True, "bpm": 120.0},
                    "active_notes": {"0": [60, 67]},
                    "module_outputs": {},
                }
            },
            {"kind": "clock"},
        )

        result = run_research(contract)
        self.assertEqual(result["note_density"], "medium")
        self.assertEqual(contract.contract_version, current_contract_version())

    def test_research_contract_forward_compatible_additive_minor(self):
        contract = ResearchContract(
            contract_version="1.1",
            schema_version=7,
            snapshot_timestamp=300.0,
            event_kind="clock",
            transport=freeze_payload({"tick": 7, "bar": 1, "running": True, "bpm": 120.0}),
            active_notes=freeze_payload({"0": [60, 64, 67]}),
            module_outputs=freeze_payload({"future_additive_field": {"debug": True}}),
        )

        result = run_research(contract)
        self.assertEqual(result["active_note_total"], 3)
        self.assertNotIn("status", result)

    def test_research_contract_breaking_version_mismatch_fails_deterministically(self):
        contract = ResearchContract(
            contract_version="2.0",
            schema_version=7,
            snapshot_timestamp=300.0,
            event_kind="clock",
            transport=freeze_payload({"tick": 9, "bar": 1, "running": True, "bpm": 120.0}),
            active_notes=freeze_payload({"0": [72]}),
            module_outputs=freeze_payload({}),
        )

        result = run_research(contract)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "deep_research_contract_incompatible")
        self.assertEqual(result["error"]["expected_contract_version"], current_contract_version())
        self.assertEqual(result["error"]["actual_contract_version"], "2.0")


if __name__ == "__main__":
    unittest.main()
