import unittest
from pathlib import Path


class StartupProfileTuiModeTest(unittest.TestCase):
    def test_run_tui_is_default_profile(self):
        src = Path("midicrt.py").read_text(encoding="utf-8")
        self.assertIn('ACTIVE_PROFILE = "run_tui"', src)
        self.assertIn('default="run_tui"', src)

    def test_pixel_renderer_import_is_gated_to_run_pixel_branch(self):
        src = Path("midicrt.py").read_text(encoding="utf-8")
        self.assertIn('elif selected == "run_pixel":', src)
        self.assertIn('from ui.renderers.pixel import PixelRenderer', src)
        self.assertIn('MIDICRT_ENABLE_PIXEL', src)


if __name__ == "__main__":
    unittest.main()
