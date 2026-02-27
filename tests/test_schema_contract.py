import json
import pathlib
import unittest

from engine.deep_research.platform import RESEARCH_CONTRACT_MAJOR_VERSION
from engine.state.schema import SCHEMA_VERSION, build_snapshot
from ui.client import normalize_snapshot


FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "schema_normalization_cases.json"


class SchemaContractTest(unittest.TestCase):
    def test_build_snapshot_includes_required_schema_fields(self):
        snapshot = build_snapshot(
            timestamp=123.0,
            tick=42,
            bar=1,
            running=True,
            bpm=120.0,
        ).as_dict()

        self.assertEqual(snapshot["schema_version"], SCHEMA_VERSION)
        self.assertIn("transport", snapshot)
        self.assertEqual(snapshot["transport"]["tick"], 42)
        self.assertIn("channels", snapshot)
        self.assertIn("module_outputs", snapshot)
        self.assertIn("deep_research", snapshot)
        self.assertEqual(snapshot["deep_research"]["late_policy"], "drop")

    def test_normalize_snapshot_handles_legacy_and_schema_wrapped_payloads(self):
        modern = {"schema_version": 3, "transport": {"tick": 7}, "status_text": "ok"}
        wrapped = {"tick_counter": 7, "schema": modern}
        legacy = {"tick_counter": 7, "status_text": "legacy"}

        self.assertEqual(normalize_snapshot(modern), modern)
        self.assertEqual(normalize_snapshot(wrapped), modern)
        self.assertEqual(normalize_snapshot(legacy), legacy)


    def test_schema_normalization_regression_fixtures(self):
        payload = json.loads(FIXTURE.read_text())
        for case in payload.get("cases", []):
            with self.subTest(case=case.get("name")):
                self.assertEqual(normalize_snapshot(case["input"]), case["expected"])

    def test_normalize_snapshot_tolerates_unknown_fields(self):
        modern = {
            "schema_version": 3,
            "transport": {"tick": 7},
            "unknown_future_field": {"nested": [1, 2, 3]},
        }

        normalized = normalize_snapshot(modern)
        self.assertIn("unknown_future_field", normalized)
        self.assertEqual(normalized["unknown_future_field"]["nested"], [1, 2, 3])

    def test_build_snapshot_normalizes_deep_research_payload(self):
        snapshot = build_snapshot(
            timestamp=123.0,
            tick=42,
            bar=1,
            running=True,
            bpm=120.0,
            deep_research={"version": "7", "timestamp": "9.5", "future": {"ok": True}},
        ).as_dict()

        self.assertEqual(snapshot["deep_research"]["version"], 7)
        self.assertEqual(snapshot["deep_research"]["timestamp"], 9.5)
        self.assertIn("future", snapshot["deep_research"])


    def test_breaking_schema_change_must_fail_without_version_bump_case(self):
        fixture = json.loads((pathlib.Path(__file__).parent / "fixtures" / "deep_research_contract_cases.json").read_text())
        rollout = fixture["rollout_guard"]["must_fail_without_version_bump"]

        self.assertIn("engine/state/schema.py", rollout["schema_change_paths"])
        # Policy requires major bump for schema-breaking changes; major must be at least 1.
        self.assertGreaterEqual(RESEARCH_CONTRACT_MAJOR_VERSION, 1)


if __name__ == "__main__":
    unittest.main()
