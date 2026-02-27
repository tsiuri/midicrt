from pathlib import Path
import sys
import types
import unittest

if "blessed" not in sys.modules:
    blessed = types.ModuleType("blessed")

    class _Terminal:
        number_of_colors = 0

        @staticmethod
        def strip_seqs(text):
            return text

        @staticmethod
        def reverse(text):
            return text

        @staticmethod
        def bold(text):
            return text

        @staticmethod
        def color(_n):
            return lambda text: text

    blessed.Terminal = _Terminal
    sys.modules["blessed"] = blessed

from ui.composition import build_footer_widget
from ui.model import Column, FooterStatusWidget, Frame, TextBlock, Line
from ui.renderers.pixel import PixelRenderer
from ui.renderers.text import TextRenderer


class FooterStatusWidgetTests(unittest.TestCase):
    def test_build_footer_widget_prefers_structured_footer_fields(self):
        widget = build_footer_widget(
            {
                "status_text": "ignored",
                "fps_status": "fps:60.0",
                "footer": {"left": "RUN 120", "right": "fps:59.8 | sched:ok"},
            }
        )
        self.assertEqual(widget.left, "RUN 120")
        self.assertEqual(widget.right, "fps:59.8 | sched:ok")

    def test_build_footer_widget_falls_back_to_status_and_fps_fields(self):
        widget = build_footer_widget({"status_text": "stopped", "fps_status": "fps:30.0"})
        self.assertEqual(widget.left, "stopped")
        self.assertEqual(widget.right, "fps:30.0")

    def test_text_renderer_footer_survives_page_switches_and_resizes(self):
        renderer = TextRenderer()
        page_one = Column(children=[TextBlock(lines=[Line.plain("page1")]), FooterStatusWidget(left="idle", right="fps:60.0")])
        page_two = Column(children=[TextBlock(lines=[Line.plain("page2")]), FooterStatusWidget(left="running", right="fps:59.9")])

        out_a = renderer.render(page_one, Frame(cols=40, rows=4))
        out_b = renderer.render(page_two, Frame(cols=40, rows=4))
        out_c = renderer.render(page_two, Frame(cols=28, rows=3))

        self.assertIn("idle fps:60.0", out_a[1])
        self.assertIn("running fps:59.9", out_b[1])
        self.assertIn("running fps:59.9", out_c[1])

    def test_pixel_renderer_footer_survives_page_switches_and_resizes(self):
        renderer = PixelRenderer()
        page = Column(children=[TextBlock(lines=[Line.plain("x")]), FooterStatusWidget(left="status", right="fps:60")])
        out_wide = renderer.render(page, Frame(cols=32, rows=3))
        out_narrow = renderer.render(page, Frame(cols=18, rows=3))
        self.assertEqual(len(out_wide), 3)
        self.assertEqual(len(out_narrow), 3)

    def test_compositor_renderer_has_footer_widget_branch(self):
        src = Path("fb/compositor_renderer.py").read_text(encoding="utf-8")
        self.assertIn("if isinstance(widget, FooterStatusWidget):", src)
        self.assertIn("txt = f\"{widget.left} {widget.right}\".strip()", src)


if __name__ == "__main__":
    unittest.main()
