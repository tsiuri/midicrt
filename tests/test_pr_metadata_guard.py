import importlib.util
import json
import os
import unittest
from pathlib import Path


MODULE_PATH = Path("tools/check_track_boundaries.py")
_spec = importlib.util.spec_from_file_location("check_track_boundaries", MODULE_PATH)
ctb = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(ctb)


class PrMetadataGuardTests(unittest.TestCase):
    def setUp(self):
        self.lane_roots = {
            "platform": {"engine/", ".github/workflows/"},
            "logic": {"pages/", "plugins/"},
            "qa-contract": {"tests/", "docs/"},
            "observer": {"web/"},
        }

    def test_branch_name_valid(self):
        match = ctb.BRANCH_NAME_RE.match("agent/logic/MIDI-1234-voice-monitor-fix")
        self.assertIsNotNone(match)
        self.assertEqual(match.group("lane"), "logic")

    def test_branch_name_invalid(self):
        self.assertIsNone(ctb.BRANCH_NAME_RE.match("feature/logic/1234-voice-monitor-fix"))

    def test_pr_lane_parse(self):
        body = """## Lane declaration\n- Lane: qa-contract\n"""
        self.assertEqual(ctb._parse_pr_lane(body), "qa-contract")

    def test_pr_lane_missing(self):
        self.assertIsNone(ctb._parse_pr_lane("- Ticket: TASK-1"))

    def test_valid_metadata_no_cross_lane(self):
        body = """## Lane declaration
- Lane: logic

## Contract impact
- [ ] This PR changes a contract/interface consumed by another lane (`contract-impact`).
"""
        ok, errors = ctb._validate_pr_metadata(
            "agent/logic/1234-note-page-tuning",
            body,
            {"pages/notes.py", "plugins/zharmony.py"},
            self.lane_roots,
        )
        self.assertTrue(ok)
        self.assertEqual(errors, [])

    def test_invalid_when_contract_impact_field_missing(self):
        body = """## Lane declaration
- Lane: logic
"""
        ok, errors = ctb._validate_pr_metadata(
            "agent/logic/1234-note-page-tuning",
            body,
            {"pages/notes.py"},
            self.lane_roots,
        )
        self.assertFalse(ok)
        self.assertTrue(any("contract-impact declaration field" in e for e in errors))

    def test_cross_lane_requires_checked_contract_impact(self):
        body = """## Lane declaration
- Lane: logic

## Contract impact
- [ ] This PR changes a contract/interface consumed by another lane (`contract-impact`).
"""
        ok, errors = ctb._validate_pr_metadata(
            "agent/logic/1234-note-page-tuning",
            body,
            {"pages/notes.py", "tests/test_schema_contract.py"},
            self.lane_roots,
        )
        self.assertFalse(ok)
        self.assertTrue(any("Cross-lane edits detected" in e for e in errors))

    def test_cross_lane_allowed_when_contract_impact_checked(self):
        body = """## Lane declaration
- Lane: logic

## Contract impact
- [x] This PR changes a contract/interface consumed by another lane (`contract-impact`).
"""
        ok, errors = ctb._validate_pr_metadata(
            "agent/logic/1234-note-page-tuning",
            body,
            {"pages/notes.py", "tests/test_schema_contract.py"},
            self.lane_roots,
        )
        self.assertTrue(ok)
        self.assertEqual(errors, [])

    def test_override_source_precedence_env_then_label_then_file(self):
        old = dict(os.environ)
        try:
            os.environ["ALLOW_CROSS_TRACK"] = "1"
            os.environ["ALLOW_CROSS_TRACK_SOURCE"] = "ALLOW_CROSS_TRACK"
            self.assertEqual(ctb._override_source(), "ALLOW_CROSS_TRACK")

            os.environ.pop("ALLOW_CROSS_TRACK", None)
            os.environ.pop("ALLOW_CROSS_TRACK_SOURCE", None)
            os.environ["ALLOW_CROSS_TRACK_LABEL"] = "1"
            self.assertEqual(ctb._override_source(), f"label:{ctb.OVERRIDE_LABEL}")

            os.environ.pop("ALLOW_CROSS_TRACK_LABEL", None)
            marker = ctb.OVERRIDE_FILE
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("1", encoding="utf-8")
            self.assertEqual(ctb._override_source(), str(ctb.OVERRIDE_FILE))
            marker.unlink()
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_json_summary_shape(self):
        summary = {
            "track_a_files": ["engine/deep_research/platform.py"],
            "track_b_files": ["engine/deep_research/logic.py"],
            "override_source": None,
        }
        encoded = json.dumps(summary)
        parsed = json.loads(encoded)
        self.assertEqual(parsed["track_a_files"][0], "engine/deep_research/platform.py")
        self.assertEqual(parsed["track_b_files"][0], "engine/deep_research/logic.py")
        self.assertIsNone(parsed["override_source"])


if __name__ == "__main__":
    unittest.main()
