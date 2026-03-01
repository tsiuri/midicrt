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


if __name__ == "__main__":
    unittest.main()
