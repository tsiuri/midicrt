import importlib
import sys
import types
import unittest


if "blessed" not in sys.modules:
    blessed = types.ModuleType("blessed")

    class _Terminal:
        number_of_colors = 0

        def move_yx(self, *_args, **_kwargs):
            return ""

        clear_eol = ""
        normal = ""

        @staticmethod
        def reverse(text):
            return text

        @staticmethod
        def strip_seqs(text):
            return text

        @staticmethod
        def bold(text):
            return text

        @staticmethod
        def color(_n):
            return lambda text: text

    blessed.Terminal = _Terminal
    sys.modules["blessed"] = blessed

if "midicrt" not in sys.modules:
    midicrt = types.ModuleType("midicrt")
    midicrt.draw_line = lambda *_a, **_k: None
    midicrt.INSTRUMENT_NAMES = [f"Ch{i}" for i in range(1, 17)]
    midicrt.__file__ = __file__
    midicrt.term = types.SimpleNamespace(
        clear_eol="",
        move_yx=lambda *_a, **_k: "",
        reverse=lambda t: t,
        normal="",
        bold=lambda t: t,
        strip_seqs=lambda t: t,
    )
    sys.modules["midicrt"] = midicrt

if "configutil" not in sys.modules:
    configutil = types.ModuleType("configutil")
    configutil.load_section = lambda _name: {}
    configutil.save_section = lambda _name, _data: None
    configutil.load_settings = lambda: {}
    configutil.save_settings = lambda _data: None
    configutil.config_path = lambda: "config/settings.json"
    sys.modules["configutil"] = configutil

if "mido" not in sys.modules:
    mido = types.ModuleType("mido")
    mido.open_output = lambda *_a, **_k: None
    mido.Message = lambda *_a, **_k: None
    sys.modules["mido"] = mido

from ui.model import PageLinesWidget
from ui.renderers.pixel import PixelRenderer
from ui.renderers.text import TextRenderer


class PageLinesParityTest(unittest.TestCase):
    PAGE_MODULES = [
        "pages.help",
        "pages.ccmonitor",
        "pages.ccgraph",
        "pages.sendnotes",
        "pages.chordkey",
        "pages.proglog",
        "pages.audiospectrum",
        "pages.tuner",
        "pages.stuckheat",
        "pages.voicemon",
        "pages.configui",
        "pages.timesig_exp",
    ]

    @staticmethod
    def _plain_lines(renderer, widget):
        return ["".join(seg.text for seg in line.segments) for line in renderer._flatten(widget)]

    def test_page_lines_widget_text_pixel_parity(self):
        text = TextRenderer()
        pixel = PixelRenderer()
        base_state = {"cols": 80, "rows": 24, "y_offset": 3, "tick": 0, "bar": 0, "running": False, "bpm": 120.0}

        for module_name in self.PAGE_MODULES:
            with self.subTest(module=module_name):
                mod = importlib.reload(importlib.import_module(module_name))
                widget = mod.build_widget(dict(base_state))
                self.assertIsInstance(widget, PageLinesWidget)
                self.assertEqual(self._plain_lines(text, widget), self._plain_lines(pixel, widget))


if __name__ == "__main__":
    unittest.main()
