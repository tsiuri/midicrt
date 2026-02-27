import unittest

import sys
import types

if "blessed" not in sys.modules:
    class _FakeTerminal:
        number_of_colors = 0

        def strip_seqs(self, text):
            return text

        def reverse(self, text):
            return text

        def bold(self, text):
            return text

        def color(self, _):
            return lambda text: text

    sys.modules["blessed"] = types.SimpleNamespace(Terminal=_FakeTerminal)

from ui.model import Frame, OverlayEntry, OverlayLayerWidget
from ui.overlays import capture_plugin_overlay_widget, compose_overlay_rows
from ui.renderers.pixel import PixelRenderer
from ui.renderers.text import TextRenderer


class _PluginA:
    __name__ = "aplugin"

    @staticmethod
    def draw(state):
        import sys

        sys.stdout.write("\x1b[5;1HAAAA")


class _PluginB:
    __name__ = "bplugin"

    @staticmethod
    def draw():
        import sys

        sys.stdout.write("\x1b[5;1HBBBB")


class PluginOverlayWidgetTests(unittest.TestCase):
    def test_overlay_capture_and_layer_ordering(self):
        overlay = capture_plugin_overlay_widget(
            plugins=[_PluginA, _PluginB],
            state={"tick": 0},
            cols=20,
            rows=10,
            draw_takes_state={id(_PluginA): True, id(_PluginB): False},
        )
        self.assertEqual([e.plugin_id for e in overlay.entries], ["_PluginA", "_PluginB"])
        self.assertEqual([e.z_index for e in overlay.entries], [0, 1])

        rows = compose_overlay_rows(overlay, cols=20, rows=10, start_row=3)
        self.assertEqual(rows, [(4, "BBBB")])

    def test_text_pixel_overlay_widget_parity(self):
        widget = OverlayLayerWidget(
            entries=[
                OverlayEntry(plugin_id="aplugin", z_index=0, row=4, col=0, kind="badge", text="ONE"),
                OverlayEntry(plugin_id="bplugin", z_index=1, row=4, col=0, kind="alert", text="TWO"),
            ]
        )
        text = TextRenderer()
        pixel = PixelRenderer(renderer_name="sdl2")

        text_lines = ["".join(seg.text for seg in line.segments) for line in text._flatten(widget)]
        pixel_lines = ["".join(seg.text for seg in line.segments) for line in pixel._flatten(widget)]
        self.assertEqual(text_lines, pixel_lines)

        composed = compose_overlay_rows(widget, cols=20, rows=8)
        self.assertEqual(composed, [(4, "TWO")])

        frame = Frame(cols=40, rows=6)
        rendered = text.render(widget, frame)
        self.assertTrue(rendered[1].startswith("z=00 row=04"))


if __name__ == "__main__":
    unittest.main()
