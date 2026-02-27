import unittest

from ui.client import normalize_snapshot, render_snapshot


class UIClientFallbacksTest(unittest.TestCase):
    def test_render_snapshot_reports_deep_research_unavailable(self):
        lines = render_snapshot({"schema_version": 5, "timestamp": 1.0, "transport": {}}, cols=120)
        self.assertTrue(any("deep_research unavailable" in line for line in lines))

    def test_normalize_snapshot_merges_legacy_optional_metadata(self):
        raw = {
            "type": "snapshot",
            "payload": {
                "schema": {"schema_version": 5, "timestamp": 1.0, "transport": {}},
                "deep_research": {"result": {"signature": "ok"}},
            },
        }
        norm = normalize_snapshot(raw)
        self.assertIn("deep_research", norm)


if __name__ == "__main__":
    unittest.main()
