import sys
import types
import unittest

if "mido" not in sys.modules:
    mido = types.ModuleType("mido")

    class _Msg:
        def __init__(self, *_a, **_k):
            pass

    mido.Message = _Msg
    sys.modules["mido"] = mido

from engine.memory.session_model import TempoSegment, build_session_model
from engine.memory.tempo_timeline import TempoTimeline


class TempoTimelineTest(unittest.TestCase):
    def test_piecewise_tick_to_seconds(self):
        sess = build_session_model(
            session_id="s1",
            start_tick=0,
            bpm=120.0,
            ppqn=24,
            tempo_segments=[
                TempoSegment(start_tick=0, bpm=120.0),
                TempoSegment(start_tick=96, bpm=60.0),
            ],
        )
        tl = TempoTimeline.from_session(sess)
        self.assertAlmostEqual(tl.tick_to_seconds(96), 2.0, places=6)
        self.assertAlmostEqual(tl.tick_to_seconds(192), 6.0, places=6)

    def test_project_tick_tempo_relative(self):
        sess = build_session_model(
            session_id="s2",
            start_tick=0,
            bpm=120.0,
            ppqn=24,
            tempo_segments=[
                TempoSegment(start_tick=0, bpm=120.0),
                TempoSegment(start_tick=96, bpm=60.0),
            ],
        )
        tl = TempoTimeline.from_session(sess)
        projected = tl.project_tick(192, current_bpm=120.0, anchor_tick=0)
        self.assertAlmostEqual(projected, 288.0, places=6)


if __name__ == "__main__":
    unittest.main()
