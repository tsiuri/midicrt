import unittest
from pathlib import Path


class DeepResearchCoreFlagGateTest(unittest.TestCase):
    def test_core_contains_execution_and_surface_gates(self):
        src = Path("engine/core.py").read_text(encoding="utf-8")
        self.assertIn('enable_module_execution', src)
        self.assertIn('enable_ui_surface_module_outputs', src)
        self.assertIn('enable_ui_surface_views', src)
        self.assertIn('enable_ui_surface_deep_research', src)


if __name__ == "__main__":
    unittest.main()
