import json
import pathlib
import tempfile
import unittest

import mido

from engine.memory.capture import MemoryCaptureManager

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "memory_same_tick_stream.json"


class MemoryCaptureManagerTest(unittest.TestCase):
    def _manager(self, tmp):
        return MemoryCaptureManager(max_sessions=8, export_dir=tmp, project_root=tmp)

    def test_start_stop_lifecycle_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp)
            self.assertFalse(mgr.status()["armed"])

            armed = mgr.memory_start(tick=0, bpm=120.0, running=False)
            self.assertTrue(armed)
            self.assertTrue(mgr.status()["armed"])

            mgr.on_transport(tick=0, bpm=120.0, running=True, prev_running=False)
            status = mgr.status()
            self.assertTrue(status["current_id"].startswith("engine-memory-"))
            self.assertEqual(status["current_start_tick"], 0)

            armed_after_stop = mgr.memory_stop(tick=12)
            self.assertFalse(armed_after_stop)
            status_after = mgr.status()
            self.assertFalse(status_after["armed"])
            self.assertEqual(status_after["current_id"], "")
            self.assertEqual(status_after["sessions"], 1)

    def test_cc123_and_zero_velocity_note_on_close_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp)
            mgr.memory_start(tick=0, bpm=120.0, running=True)

            note_on = mido.Message("note_on", channel=0, note=60, velocity=100)
            mgr.on_event(event={"kind": "note_on"}, msg=note_on, tick=2)

            zero_vel = mido.Message("note_on", channel=0, note=60, velocity=0)
            mgr.on_event(event={"kind": "note_on"}, msg=zero_vel, tick=5)

            note_on2 = mido.Message("note_on", channel=0, note=64, velocity=90)
            mgr.on_event(event={"kind": "note_on"}, msg=note_on2, tick=7)

            cc123 = mido.Message("control_change", channel=0, control=123, value=0)
            mgr.on_event(event={"kind": "control_change"}, msg=cc123, tick=9)

            mgr.memory_stop(tick=12)
            sid = mgr.memory_list()[0]["id"]
            session = mgr.memory_get(sid)
            self.assertIsNotNone(session)

            spans = sorted((s.start_tick, s.end_tick, s.pitch, s.channel) for s in session.note_spans)
            self.assertEqual(spans, [(2, 5, 60, 1), (7, 9, 64, 1)])

    def test_overlapping_same_note_creates_two_spans(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp)
            mgr.memory_start(tick=0, bpm=120.0, running=True)

            mgr.on_event(event={"kind": "note_on"}, msg=mido.Message("note_on", channel=0, note=60, velocity=100), tick=1)
            mgr.on_event(event={"kind": "note_on"}, msg=mido.Message("note_on", channel=0, note=60, velocity=70), tick=4)
            mgr.on_event(event={"kind": "note_off"}, msg=mido.Message("note_off", channel=0, note=60, velocity=0), tick=8)
            mgr.memory_stop(tick=10)

            sid = mgr.memory_list()[0]["id"]
            session = mgr.memory_get(sid)
            spans = sorted((s.start_tick, s.end_tick, s.pitch, s.velocity) for s in session.note_spans)
            self.assertEqual(spans, [(1, 4, 60, 100), (4, 8, 60, 70)])

    def test_tempo_and_meter_segments_append_with_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp)
            mgr.memory_start(tick=0, bpm=120.0, running=False)

            # Session starts with an initial base segment.
            mgr.on_transport(
                tick=0,
                bpm=120.0,
                running=True,
                prev_running=False,
                meter_estimate="4/4",
                meter_confidence=0.9,
            )
            # Suppressed: bpm delta below hysteresis threshold.
            mgr.on_transport(tick=12, bpm=120.2, running=True, prev_running=True, meter_estimate="4/4", meter_confidence=0.9)
            # Appended: bpm delta above threshold and enough tick spacing.
            mgr.on_transport(tick=24, bpm=121.0, running=True, prev_running=True, meter_estimate="4/4", meter_confidence=0.9)
            # Suppressed: same meter label.
            mgr.on_transport(tick=30, bpm=121.0, running=True, prev_running=True, meter_estimate="4/4", meter_confidence=0.9)
            # Suppressed: confidence too low.
            mgr.on_transport(tick=36, bpm=121.6, running=True, prev_running=True, meter_estimate="3/4", meter_confidence=0.2)
            # Appended: confident meter label change.
            mgr.on_transport(tick=48, bpm=122.0, running=True, prev_running=True, meter_estimate="3/4", meter_confidence=0.95)

            live = mgr.memory_get_current_display()
            self.assertIsNotNone(live)
            self.assertEqual([(seg.start_tick, seg.bpm) for seg in live.header.tempo_segments], [(0, 120.0), (24, 121.0), (36, 121.6), (48, 122.0)])
            self.assertEqual(
                [(seg.start_tick, seg.numerator, seg.denominator) for seg in live.header.time_signature_segments],
                [(0, 4, 4), (48, 3, 4)],
            )


if __name__ == "__main__":
    unittest.main()
