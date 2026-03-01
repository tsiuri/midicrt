import unittest

from engine.memory.session_model import TempoSegment, build_session_model
from engine.memory.storage import build_index_record
from engine.memory.tempo_timeline import (
    duration_seconds,
    normalize_tempo_segments,
    seconds_to_tick,
    tick_to_seconds,
)


class TempoTimelineTest(unittest.TestCase):
    def test_normalize_sorts_dedupes_and_filters_invalid_bpms(self):
        segments = [
            TempoSegment(start_tick=24, bpm=90.0),
            {"start_tick": 0, "bpm": 120.0},
            {"start_tick": 24, "bpm": 100.0},
            {"start_tick": 12, "bpm": 0.0},
            {"start_tick": 18, "bpm": -1.0},
        ]
        self.assertEqual(normalize_tempo_segments(segments), [(0, 120.0), (24, 100.0)])

    def test_tick_to_seconds_and_seconds_to_tick_respect_segments(self):
        segments = [TempoSegment(start_tick=0, bpm=120.0), TempoSegment(start_tick=24, bpm=60.0)]
        self.assertAlmostEqual(
            tick_to_seconds(48, start_tick=0, ppqn=24, segments=segments, fallback_bpm=120.0),
            1.5,
            places=6,
        )
        self.assertEqual(
            seconds_to_tick(1.5, start_tick=0, ppqn=24, segments=segments, fallback_bpm=120.0),
            48,
        )

    def test_multi_segment_tick_to_seconds_piecewise_integrates(self):
        segments = [
            TempoSegment(start_tick=0, bpm=120.0),
            TempoSegment(start_tick=24, bpm=60.0),
            TempoSegment(start_tick=48, bpm=180.0),
        ]
        # 0..24 @120 bpm => 0.5 s, 24..48 @60 bpm => 1.0 s, 48..72 @180 bpm => 0.333333 s
        self.assertAlmostEqual(
            tick_to_seconds(72, start_tick=0, ppqn=24, segments=segments, fallback_bpm=120.0),
            1.8333333333,
            places=6,
        )

    def test_seconds_to_tick_is_inverse_consistent_within_rounding_tolerance(self):
        segments = [
            TempoSegment(start_tick=0, bpm=120.0),
            TempoSegment(start_tick=18, bpm=90.0),
            TempoSegment(start_tick=40, bpm=150.0),
        ]
        for tick in [0, 1, 5, 18, 19, 27, 40, 47, 63, 96]:
            sec = tick_to_seconds(tick, start_tick=0, ppqn=24, segments=segments, fallback_bpm=120.0)
            tick_roundtrip = seconds_to_tick(sec, start_tick=0, ppqn=24, segments=segments, fallback_bpm=120.0)
            self.assertLessEqual(abs(tick_roundtrip - tick), 1, msg=f"roundtrip mismatch at tick={tick}")

    def test_monotonic_and_boundary_ticks_are_continuous(self):
        segments = [
            TempoSegment(start_tick=0, bpm=120.0),
            TempoSegment(start_tick=24, bpm=60.0),
            TempoSegment(start_tick=48, bpm=180.0),
        ]
        samples = [tick_to_seconds(t, start_tick=0, ppqn=24, segments=segments, fallback_bpm=120.0) for t in range(0, 73)]
        self.assertTrue(all(a <= b for a, b in zip(samples, samples[1:])), "timeline must be monotonic")

        before_24 = tick_to_seconds(23, start_tick=0, ppqn=24, segments=segments, fallback_bpm=120.0)
        at_24 = tick_to_seconds(24, start_tick=0, ppqn=24, segments=segments, fallback_bpm=120.0)
        after_24 = tick_to_seconds(25, start_tick=0, ppqn=24, segments=segments, fallback_bpm=120.0)
        self.assertAlmostEqual(at_24 - before_24, 1.0 / 48.0, places=6)  # 120 bpm step
        self.assertAlmostEqual(after_24 - at_24, 1.0 / 24.0, places=6)  # 60 bpm step

        before_48 = tick_to_seconds(47, start_tick=0, ppqn=24, segments=segments, fallback_bpm=120.0)
        at_48 = tick_to_seconds(48, start_tick=0, ppqn=24, segments=segments, fallback_bpm=120.0)
        after_48 = tick_to_seconds(49, start_tick=0, ppqn=24, segments=segments, fallback_bpm=120.0)
        self.assertAlmostEqual(at_48 - before_48, 1.0 / 24.0, places=6)  # 60 bpm step
        self.assertAlmostEqual(after_48 - at_48, 1.0 / 72.0, places=6)  # 180 bpm step

    def test_duration_seconds_falls_back_to_scalar_bpm_when_segments_missing(self):
        self.assertAlmostEqual(
            duration_seconds(0, 48, ppqn=24, segments=[], fallback_bpm=120.0),
            1.0,
            places=6,
        )

    def test_build_index_record_uses_tempo_segments(self):
        session = build_session_model(
            session_id="s1",
            start_tick=0,
            bpm=120.0,
            ppqn=24,
            tempo_segments=[TempoSegment(start_tick=0, bpm=120.0), TempoSegment(start_tick=24, bpm=60.0)],
        )
        session.header.stop_tick = 48
        rec = build_index_record(session, session_path="", origin="capture")
        self.assertAlmostEqual(float(rec["duration_seconds"]), 1.5, places=6)

    def test_build_index_record_duration_uses_piecewise_tempo_segments(self):
        session = build_session_model(
            session_id="s2",
            start_tick=0,
            bpm=120.0,
            ppqn=24,
            tempo_segments=[
                TempoSegment(start_tick=0, bpm=120.0),
                TempoSegment(start_tick=24, bpm=60.0),
                TempoSegment(start_tick=48, bpm=180.0),
            ],
        )
        session.header.stop_tick = 72
        rec = build_index_record(session, session_path="", origin="capture")
        expected = duration_seconds(0, 72, ppqn=24, segments=session.header.tempo_segments, fallback_bpm=120.0)
        self.assertAlmostEqual(float(rec["duration_seconds"]), expected, places=6)


if __name__ == "__main__":
    unittest.main()
