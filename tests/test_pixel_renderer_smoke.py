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

from ui.model import Frame, PianoRollCell, PianoRollWidget
from ui.renderers.pixel import PixelRenderer


class PixelRendererSmokeTest(unittest.TestCase):
    def test_construct_and_render_pianoroll(self):
        renderer = PixelRenderer()
        widget = PianoRollWidget(
            pitches=[60],
            cells=[[PianoRollCell(velocity=90, channel=1)]],
            timeline="|",
        )
        out = renderer.render(widget, Frame(cols=20, rows=4))
        self.assertEqual(len(out), 4)


if __name__ == "__main__":
    unittest.main()
