import unittest

from engine.deep_research.platform import build_contract, resolve_feature_flags


class DeepResearchFeatureFlagsTest(unittest.TestCase):
    def test_contract_module_outputs_respect_feature_flags(self):
        snapshot = {
            "schema": {
                "schema_version": 5,
                "timestamp": 123.0,
                "transport": {"tick": 7},
                "active_notes": {},
                "module_outputs": {
                    "alpha": {"x": 1, "views": {"hidden": True}},
                    "beta": {"y": 2},
                },
                "deep_research": {
                    "feature_flags": {
                        "enable_contract_module_outputs": True,
                        "enable_contract_views": False,
                    }
                },
            }
        }
        contract = build_contract(snapshot, {"kind": "clock"})
        module_outputs = dict(contract.module_outputs)
        self.assertIn("alpha", module_outputs)
        self.assertNotIn("views", module_outputs["alpha"])
        self.assertEqual(module_outputs["beta"]["y"], 2)

    def test_contract_can_disable_module_outputs(self):
        snapshot = {
            "schema": {
                "schema_version": 5,
                "timestamp": 123.0,
                "transport": {"tick": 7},
                "active_notes": {},
                "module_outputs": {"alpha": {"x": 1}},
                "deep_research": {"feature_flags": {"enable_contract_module_outputs": False}},
            }
        }
        contract = build_contract(snapshot, {"kind": "clock"})
        self.assertEqual(dict(contract.module_outputs), {})

    def test_flag_resolver_defaults_are_enabled(self):
        flags = resolve_feature_flags(None)
        self.assertTrue(flags["enable_module_execution"])
        self.assertTrue(flags["enable_ui_surface_deep_research"])


if __name__ == "__main__":
    unittest.main()
