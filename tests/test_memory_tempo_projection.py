import importlib
import sys
import types
import typing
import unittest

from engine.memory.tempo_timeline import seconds_to_tick, tick_to_seconds


if not hasattr(typing, "NotRequired"):
    try:
        from typing_extensions import NotRequired as _NotRequired

        typing.NotRequired = _NotRequired
    except Exception:  # pragma: no cover
        typing.NotRequired = object


def _project_span_width_ticks(*, start_tick, end_tick, start_ref_tick, ppqn, segments, mode, current_bpm):
    if mode == "beat":
        return int(end_tick) - int(start_tick)

    start_sec = tick_to_seconds(
        int(start_tick),
        start_tick=int(start_ref_tick),
        ppqn=int(ppqn),
        segments=segments,
        fallback_bpm=float(current_bpm),
    )
    end_sec = tick_to_seconds(
        int(end_tick),
        start_tick=int(start_ref_tick),
        ppqn=int(ppqn),
        segments=segments,
        fallback_bpm=float(current_bpm),
    )

    start_equiv = seconds_to_tick(
        start_sec,
        start_tick=0,
        ppqn=int(ppqn),
        segments=[],
        fallback_bpm=float(current_bpm),
    )
    end_equiv = seconds_to_tick(
        end_sec,
        start_tick=0,
        ppqn=int(ppqn),
        segments=[],
        fallback_bpm=float(current_bpm),
    )
    return int(end_equiv) - int(start_equiv)


class MemoryTempoProjectionTest(unittest.TestCase):
    def test_beat_mode_remains_tick_linear_for_spans_before_and_after_tempo_changes(self):
        segments = [
            {"start_tick": 0, "bpm": 120.0},
            {"start_tick": 48, "bpm": 60.0},
        ]
        before_change = _project_span_width_ticks(
            start_tick=12,
            end_tick=36,
            start_ref_tick=0,
            ppqn=24,
            segments=segments,
            mode="beat",
            current_bpm=60.0,
        )
        after_change = _project_span_width_ticks(
            start_tick=60,
            end_tick=84,
            start_ref_tick=0,
            ppqn=24,
            segments=segments,
            mode="beat",
            current_bpm=60.0,
        )
        self.assertEqual(before_change, 24)
        self.assertEqual(after_change, 24)

    def test_tempo_relative_lower_current_bpm_squishes_older_faster_spans(self):
        segments = [
            {"start_tick": 0, "bpm": 120.0},
            {"start_tick": 48, "bpm": 60.0},
        ]
        beat_width = _project_span_width_ticks(
            start_tick=12,
            end_tick=36,
            start_ref_tick=0,
            ppqn=24,
            segments=segments,
            mode="beat",
            current_bpm=60.0,
        )
        tempo_relative_width = _project_span_width_ticks(
            start_tick=12,
            end_tick=36,
            start_ref_tick=0,
            ppqn=24,
            segments=segments,
            mode="tempo_relative",
            current_bpm=60.0,
        )
        self.assertLess(tempo_relative_width, beat_width)

    def test_tempo_relative_higher_current_bpm_stretches_older_slower_spans(self):
        segments = [
            {"start_tick": 0, "bpm": 120.0},
            {"start_tick": 48, "bpm": 60.0},
        ]
        beat_width = _project_span_width_ticks(
            start_tick=60,
            end_tick=84,
            start_ref_tick=0,
            ppqn=24,
            segments=segments,
            mode="beat",
            current_bpm=180.0,
        )
        tempo_relative_width = _project_span_width_ticks(
            start_tick=60,
            end_tick=84,
            start_ref_tick=0,
            ppqn=24,
            segments=segments,
            mode="tempo_relative",
            current_bpm=180.0,
        )
        self.assertGreater(tempo_relative_width, beat_width)

    def test_pianoroll_payload_contract_fields_remain_present(self):
        if "blessed" not in sys.modules:
            blessed = types.ModuleType("blessed")

            class _Terminal:
                number_of_colors = 0

            _Terminal.move_yx = lambda *_a, **_k: ""
            _Terminal.clear_eol = ""
            _Terminal.normal = ""
            _Terminal.reverse = staticmethod(lambda text: text)
            _Terminal.strip_seqs = staticmethod(lambda text: text)
            _Terminal.bold = staticmethod(lambda text: text)
            _Terminal.color = staticmethod(lambda _n: (lambda text: text))
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

        pr = importlib.import_module("pages.pianoroll")
        pr = importlib.reload(pr)

        payload = pr._coerce_pianoroll_payload(
            {
                "tick_right": 96,
                "tick_now": 96,
                "columns": [[(64, 1, 100)]],
                "spans": [[80, 96, 64, 1, 100]],
            },
            roll_cols=4,
            pitch_low_val=60,
            pitch_high_val=72,
        )

        for key in (
            "time_cols",
            "tick_right",
            "tick_now",
            "active_count",
            "active_notes",
            "recent_hits",
            "spans",
            "overflow_flags",
            "overflow",
            "columns",
        ):
            self.assertIn(key, payload)


if __name__ == "__main__":
    unittest.main()
