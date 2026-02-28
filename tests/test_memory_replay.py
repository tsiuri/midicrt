import json
import pathlib
import tempfile
import unittest

import mido

from engine.memory.capture import MemoryCaptureManager

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "memory_same_tick_stream.json"


class MemoryReplayDeterminismTest(unittest.TestCase):
    def test_replay_ordering_is_deterministic_for_same_tick_multievents(self):
        payload = json.loads(FIXTURE.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            mgr = MemoryCaptureManager(max_sessions=8, export_dir=tmp, project_root=tmp)
            mgr.memory_start(tick=0, bpm=120.0, running=False)

            loop = payload["loops"][0]
            mgr.on_transport(tick=loop["start_tick"], bpm=120.0, running=True, prev_running=False)
            for row in loop["events"]:
                msg = mido.Message(row["type"], **{k: v for k, v in row.items() if k not in {"type", "tick"}})
                mgr.on_event(event={"kind": row["type"]}, msg=msg, tick=row["tick"])
            mgr.on_transport(tick=loop["stop_tick"], bpm=120.0, running=False, prev_running=True)

            sid = mgr.memory_list()[0]["id"]
            session = mgr.memory_get(sid)
            canonical = [(ev.tick, ev.seq, ev.kind, ev.channel, ev.note, ev.control) for ev in session.events]
            self.assertEqual(
                canonical,
                [
                    (1, 1, "note_on", 1, 60, None),
                    (1, 2, "control_change", 1, None, 1),
                    (6, 3, "note_off", 1, 60, None),
                ],
            )

    def test_loop_boundaries_generate_separate_sessions(self):
        payload = json.loads(FIXTURE.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            mgr = MemoryCaptureManager(max_sessions=8, export_dir=tmp, project_root=tmp)
            mgr.memory_start(tick=0, bpm=120.0, running=False)

            prev_running = False
            for loop in payload["loops"]:
                mgr.on_transport(tick=loop["start_tick"], bpm=120.0, running=True, prev_running=prev_running)
                prev_running = True
                for row in loop["events"]:
                    msg = mido.Message(row["type"], **{k: v for k, v in row.items() if k not in {"type", "tick"}})
                    mgr.on_event(event={"kind": row["type"]}, msg=msg, tick=row["tick"])
                mgr.on_transport(tick=loop["stop_tick"], bpm=120.0, running=False, prev_running=prev_running)
                prev_running = False

            sessions = mgr.memory_list()
            self.assertEqual(len(sessions), 2)
            ids = [row["id"] for row in sessions]
            first = mgr.memory_get(ids[0])
            second = mgr.memory_get(ids[1])

            self.assertEqual((first.header.start_tick, first.header.stop_tick), (0, 8))
            self.assertEqual((second.header.start_tick, second.header.stop_tick), (8, 16))
            self.assertEqual([ev.note for ev in first.events if ev.kind == "note_on"], [60])
            self.assertEqual([ev.note for ev in second.events if ev.kind == "note_on"], [64])


if __name__ == "__main__":
    unittest.main()
