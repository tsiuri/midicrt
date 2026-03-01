import tempfile
import unittest
from pathlib import Path
import importlib.util


_MODULE_PATH = Path(__file__).resolve().parents[1] / "engine" / "modules" / "pianoroll_state.py"
_SPEC = importlib.util.spec_from_file_location("pianoroll_state_under_test", _MODULE_PATH)
prs = importlib.util.module_from_spec(_SPEC)
assert _SPEC is not None and _SPEC.loader is not None
_SPEC.loader.exec_module(prs)


class PianoRollStateSamplingTest(unittest.TestCase):
    def test_steps_over_cap_use_deterministic_samples(self):
        state = prs.PianoRollState(ticks_per_col=6, idle_scroll_bpm=120.0, out_range_hold=2.5)
        state._ensure_cols(roll_cols=24)
        state.active[(1, 60)] = (100, 0)
        state.recent_hits.append((60, 1, 100, 99.9))

        state.on_tick(
            tick=180,
            running=True,
            bpm=240.0,
            roll_cols=24,
            pitch_low=36,
            pitch_high=83,
            now=100.0,
        )

        payload = state.get_view_payload(pitch_low=36, pitch_high=83, roll_cols=24, now=100.0)
        non_empty = [col for col in payload["columns"] if col]
        self.assertEqual(len(non_empty), prs.MAX_STEPS_PER_FRAME)
        self.assertEqual(state.last_tick, 180)

        offsets = state._sample_offsets(30, prs.MAX_STEPS_PER_FRAME)
        self.assertEqual(offsets[0], 0)
        self.assertEqual(offsets[-1], 29)
        self.assertEqual(len(offsets), prs.MAX_STEPS_PER_FRAME)

    def test_idle_scroll_keeps_advancing_when_stopped(self):
        state = prs.PianoRollState(ticks_per_col=6, idle_scroll_bpm=120.0, out_range_hold=2.5)
        state._ensure_cols(roll_cols=24)
        state.active[(1, 64)] = (90, 0)

        state.on_tick(
            tick=0,
            running=False,
            bpm=0.0,
            roll_cols=24,
            pitch_low=36,
            pitch_high=83,
            now=100.0,
        )
        state.on_tick(
            tick=0,
            running=False,
            bpm=0.0,
            roll_cols=24,
            pitch_low=36,
            pitch_high=83,
            now=103.0,
        )

        # 120 bpm => 48 ticks/sec; 3s -> 144 virtual ticks => 24 logical steps.
        self.assertGreaterEqual(state.last_raw_tick, 143.0)
        self.assertEqual(state.last_tick, 0)

        payload = state.get_view_payload(pitch_low=36, pitch_high=83, roll_cols=24, now=103.0)
        non_empty = [col for col in payload["columns"] if col]
        self.assertEqual(len(non_empty), prs.MAX_STEPS_PER_FRAME)

    def test_trace_includes_requested_fields(self):
        state = prs.PianoRollState(ticks_per_col=6, idle_scroll_bpm=120.0, out_range_hold=2.5)
        with tempfile.TemporaryDirectory() as tmpdir:
            trace_path = Path(tmpdir) / "trace.log"
            old_path = prs.TRACE_LOG_PATH
            try:
                prs.TRACE_LOG_PATH = str(trace_path)
                state.on_tick(
                    tick=12,
                    running=True,
                    bpm=120.0,
                    roll_cols=24,
                    pitch_low=36,
                    pitch_high=83,
                    now=200.0,
                )
            finally:
                prs.TRACE_LOG_PATH = old_path

            line = trace_path.read_text(encoding="utf-8").strip()
            self.assertIn("steps=2", line)
            self.assertIn("active=0", line)
            self.assertIn("recent_hits=0", line)
            self.assertIn("loop_ms=", line)


if __name__ == "__main__":
    unittest.main()
