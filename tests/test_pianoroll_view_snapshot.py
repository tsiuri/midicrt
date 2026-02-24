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

    def _draw_line(*_args, **_kwargs):
        return None

    midicrt.draw_line = _draw_line
    sys.modules["midicrt"] = midicrt

if "configutil" not in sys.modules:
    configutil = types.ModuleType("configutil")
    configutil.load_section = lambda _name: {}
    configutil.save_section = lambda _name, _data: None
    sys.modules["configutil"] = configutil


class PianoRollViewSnapshotTest(unittest.TestCase):
    def test_build_roll_view_is_deterministic_for_fixed_state_and_clock(self):
        pr = importlib.import_module("pages.pianoroll")
        pr = importlib.reload(pr)

        fixed_now = 1000.0
        pr.pitch_low = 60
        pr.visible_channels.clear()
        pr.visible_channels.update(range(1, 17))
        pr.roll_state.active.clear()
        pr.roll_state.active[(1, 60)] = 100
        pr.roll_state.active[(2, 61)] = 70
        pr.roll_state.time_cols = 2
        pr.roll_state.cols_buf.clear()
        pr.roll_state.cols_buf.extend(
            [
                [(60, 1, 100)],
                [(61, 2, 70)],
            ]
        )
        pr.roll_state.recent_hits.clear()
        pr.roll_state.recent_hits.append((62, 3, 90, fixed_now - 0.1))
        pr.roll_state.last_tick = 48
        pr.roll_state.last_above = None
        pr.roll_state.last_below = None

        state = {"cols": 40, "rows": 20, "y_offset": 3, "tick": 48, "_now": fixed_now}

        first = pr.build_roll_view(state)
        second = pr.build_roll_view(state)

        self.assertEqual(first["timeline"], second["timeline"])
        self.assertEqual(first["pitches"], second["pitches"])
        self.assertEqual(
            [[(c.velocity, c.channel) for c in row] for row in first["grid"]],
            [[(c.velocity, c.channel) for c in row] for row in second["grid"]],
        )
        self.assertEqual(first["header_left"], second["header_left"])
        self.assertEqual(first["footer_left"], second["footer_left"])


if __name__ == "__main__":
    unittest.main()
