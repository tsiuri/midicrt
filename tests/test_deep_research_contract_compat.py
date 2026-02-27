import json
import pathlib
import unittest

from engine.deep_research import ResearchContract, current_contract_version, freeze_payload, run_research
from engine.deep_research.platform import RESEARCH_CONTRACT_MAJOR_VERSION, RESEARCH_CONTRACT_MINOR_VERSION


FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "deep_research_contract_cases.json"


class DeepResearchContractCompatTest(unittest.TestCase):
    def test_current_contract_version_passes(self):
        contract = ResearchContract(
            contract_version=current_contract_version(),
            schema_version=7,
            snapshot_timestamp=300.0,
            event_kind="clock",
            transport=freeze_payload({"tick": 5, "bar": 1, "running": True, "bpm": 120.0}),
            active_notes=freeze_payload({"0": [60, 64, 67]}),
            module_outputs=freeze_payload({}),
        )

        result = run_research(contract)

        self.assertEqual(result["status"] if "status" in result else "ok", "ok")
        self.assertEqual(result["active_note_total"], 3)

    def test_additive_field_forward_compatibility(self):
        contract = ResearchContract(
            contract_version="1.1",
            schema_version=7,
            snapshot_timestamp=300.0,
            event_kind="clock",
            transport=freeze_payload({"tick": 7, "bar": 1, "running": True, "bpm": 120.0}),
            active_notes=freeze_payload({"0": [60, 64, 67]}),
            module_outputs=freeze_payload({"future_optional_payload": {"debug": True, "trace_id": "abc"}}),
        )

        result = run_research(contract)

        self.assertNotIn("status", result)
        self.assertEqual(result["note_density"], "medium")

    def test_major_mismatch_fails_deterministically(self):
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

    def test_rollout_fixture_matches_runtime_contract_version(self):
        fixture = json.loads(FIXTURE.read_text())
        expected = f"{RESEARCH_CONTRACT_MAJOR_VERSION}.{RESEARCH_CONTRACT_MINOR_VERSION}"
        self.assertEqual(fixture["rollout_guard"]["current_contract_version"], expected)

    def test_breaking_schema_change_must_fail_without_version_bump_contract(self):
        fixture = json.loads(FIXTURE.read_text())
        rollout = fixture["rollout_guard"]["must_fail_without_version_bump"]
        self.assertIn("engine/state/schema.py", rollout["schema_change_paths"])
        self.assertIn("engine/deep_research/platform.py", rollout["required_files"])


if __name__ == "__main__":
    unittest.main()
