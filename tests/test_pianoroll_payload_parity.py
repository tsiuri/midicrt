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
    sys.modules["midicrt"] = midicrt

if "configutil" not in sys.modules:
    configutil = types.ModuleType("configutil")
    configutil.load_section = lambda _name: {}
    configutil.save_section = lambda _name, _data: None
    sys.modules["configutil"] = configutil

from ui.model import Column, PianoRollWidget
from ui.renderers.pixel import PixelRenderer
from ui.renderers.text import TextRenderer


class PianoRollPayloadParityTest(unittest.TestCase):
    def setUp(self):
        self.pr = importlib.import_module("pages.pianoroll")
        self.pr = importlib.reload(self.pr)

    @staticmethod
    def _extract_roll_lines(flat_lines):
        out = []
        for line in flat_lines:
            text = "".join(seg.text for seg in line.segments)
            if "│" in text:
                out.append(text)
        return out

    @staticmethod
    def _occupancy_stats(grid_lines):
        per_row = []
        total = 0
        for line in grid_lines[1:]:  # skip Bars timeline row
            cells = line.split("│", 1)[1] if "│" in line else ""
            occupied = sum(1 for ch in cells if ch.strip())
            per_row.append(occupied)
            total += occupied
        return {"per_row": per_row, "total": total}

    def test_snapshot_replay_payload_parity_between_text_and_pixel(self):
        state = {"cols": 48, "rows": 18, "y_offset": 3, "tick": 96, "_now": 1000.0}
        payload_sequence = [
            {
                "pitch_low": 60,
                "pitch_high": 68,
                "time_cols": 16,
                "tick_right": 96,
                "active_count": 2,
                "active_notes": [[1, 64, 110], [2, 67, 80]],
                "recent_hits": [[62, 1, 90, 40]],
                "overflow_flags": {"above": False, "below": False},
                "overflow": {"above": None, "below": None, "above_count": 0, "below_count": 0},
                "columns": [[] for _ in range(14)] + [[(64, 1, 110)], [(67, 2, 80), (62, 1, 90)]],
            },
            {
                "pitch_low": 60,
                "pitch_high": 68,
                "time_cols": 16,
                "tick_right": 102,
                "active_count": 1,
                "active_notes": [[2, 67, 80]],
                "recent_hits": [[64, 1, 110, 80]],
                "overflow_flags": {"above": True, "below": False},
                "overflow": {"above": [84, 1, 1000.0], "below": None, "above_count": 0, "below_count": 0},
                "columns": [[] for _ in range(15)] + [[(67, 2, 80)]],
            },
        ]

        text_renderer = TextRenderer()
        pixel_renderer = PixelRenderer()

        for payload in payload_sequence:
            replay_state = dict(state)
            replay_state["views"] = {"pianoroll": payload}
            widget = self.pr.build_widget(replay_state)
            self.assertIsInstance(widget, Column)
            roll_widget = widget.children[1]
            self.assertIsInstance(roll_widget, PianoRollWidget)

            text_roll_lines = self._extract_roll_lines(text_renderer._flatten(roll_widget))
            pixel_roll_lines = self._extract_roll_lines(pixel_renderer._flatten(roll_widget))

            self.assertEqual(text_roll_lines, pixel_roll_lines)
            self.assertEqual(self._occupancy_stats(text_roll_lines), self._occupancy_stats(pixel_roll_lines))

    def test_direct_state_adapter_emits_payload_equivalent_columns(self):
        self.pr.pitch_low = 60
        state = {
            "cols": 40,
            "rows": 16,
            "tick": 48,
            "active_notes": {0: {60, 64}, 1: {67}},
            "_now": 1000.0,
        }
        roll_cols = max(16, state["cols"] - self.pr.LEFT_MARGIN - 2)
        payload = self.pr._resolve_pianoroll_payload(
            state,
            roll_cols=roll_cols,
            pitch_low_val=60,
            pitch_high_val=68,
            now=1000.0,
        )

        self.assertEqual(payload["active_count"], 3)
        self.assertEqual(len(payload["columns"]), roll_cols)
        self.assertEqual(sorted(payload["active_notes"]), [[1, 60, 100], [1, 64, 100], [2, 67, 100]])


if __name__ == "__main__":
    unittest.main()
