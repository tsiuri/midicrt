import unittest
import sys
import types

try:
    import mido  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    mido = types.ModuleType("mido")

    class _Message:
        def __init__(self, mtype, time=0, **kwargs):
            self.type = mtype
            self.time = int(time)
            for key, value in kwargs.items():
                setattr(self, key, value)

        @property
        def is_meta(self):
            return False

    mido.Message = _Message
    sys.modules["mido"] = mido

from engine.memory.editor import SessionEditor
from engine.memory.session_model import build_session_model


class MemoryEditorTest(unittest.TestCase):
    def _session(self):
        session = build_session_model(session_id="s0", start_tick=0, bpm=120.0, ppqn=24)
        session.append_normalized_event(kind="note_on", tick=2, channel=1, note=60, velocity=90)
        session.append_normalized_event(kind="control_change", tick=3, channel=1, control=1, value=32)
        session.append_normalized_event(kind="control_change", tick=4, channel=1, control=1, value=64)
        session.append_normalized_event(kind="program_change", tick=5, channel=1, program=2)
        session.append_normalized_event(kind="note_off", tick=8, channel=1, note=60, velocity=0)
        session.append_normalized_event(kind="note_on", tick=10, channel=2, note=65, velocity=70)
        session.append_normalized_event(kind="note_off", tick=14, channel=2, note=65, velocity=0)
        session.header.stop_tick = 14
        return session

    def test_quantize_nudge_transpose_velocity_and_undo_redo(self):
        source = self._session()
        editor = SessionEditor(source)

        editor.apply({"type": "set_selection", "tick_start": 0, "tick_end": 9, "channels": [1]})
        editor.apply({"type": "quantize", "grid": 4})
        editor.apply({"type": "nudge", "delta_ticks": 1})
        editor.apply({"type": "transpose", "semitones": 2})
        current = editor.apply({"type": "velocity", "scale": 0.5, "offset": 10})

        note_on = [e for e in current.events if e.kind == "note_on" and e.channel == 1][0]
        self.assertEqual(note_on.tick, 1)
        self.assertEqual(note_on.note, 62)
        self.assertEqual(note_on.velocity, 55)

        undone = editor.undo()
        self.assertIsNotNone(undone)
        note_on_undo = [e for e in undone.events if e.kind == "note_on" and e.channel == 1][0]
        self.assertEqual(note_on_undo.velocity, 90)

        redone = editor.redo()
        self.assertIsNotNone(redone)
        note_on_redo = [e for e in redone.events if e.kind == "note_on" and e.channel == 1][0]
        self.assertEqual(note_on_redo.velocity, 55)

        source_after = source.events[0]
        self.assertEqual(source_after.tick, 2)
        self.assertEqual(source_after.note, 60)

    def test_cc_program_split_merge_copy_paste(self):
        editor = SessionEditor(self._session())
        editor.apply({"type": "set_selection", "tick_start": 0, "tick_end": 10, "channels": [1]})

        current = editor.apply({"type": "cc_scale", "controls": [1], "scale": 2.0, "offset": 1})
        cc_values = [e.value for e in current.events if e.kind == "control_change" and e.channel == 1]
        self.assertEqual(cc_values, [65, 127])

        current = editor.apply({"type": "cc_thin", "controls": [1], "step": 2})
        self.assertEqual(len([e for e in current.events if e.kind == "control_change" and e.channel == 1]), 1)

        current = editor.apply({"type": "program_change_set", "channel": 1, "tick": 5, "program": 9, "replace": True})
        programs = [e.program for e in current.events if e.kind == "program_change" and e.channel == 1 and e.tick == 5]
        self.assertEqual(programs, [9])

        editor.apply({"type": "split_clip", "tick": 6})
        editor.apply({"type": "merge_clips", "first_index": 0, "second_index": 1})
        editor.apply({"type": "copy_region", "tick_start": 0, "tick_end": 8, "channels": [1]})
        current = editor.apply({"type": "paste_region", "dest_tick": 20, "channel": 3})

        pasted = [e for e in current.events if e.channel == 3 and e.tick >= 20]
        self.assertTrue(pasted)

        span_channels = {span.channel for span in current.note_spans}
        self.assertIn(3, span_channels)

    def test_apply_creates_save_as_revision_chain(self):
        editor = SessionEditor(self._session())
        base_history = editor.revision_history
        self.assertEqual(len(base_history), 1)
        self.assertEqual(base_history[0].revision_id, "r0")
        self.assertIsNone(base_history[0].parent_revision_id)

        r1_session = editor.apply({"type": "quantize", "grid": 4})
        r2_session = editor.apply({"type": "nudge", "delta_ticks": 2})

        history = editor.revision_history
        self.assertEqual([rev.revision_id for rev in history], ["r0", "r1", "r2"])
        self.assertEqual(history[1].parent_revision_id, "r0")
        self.assertEqual(history[2].parent_revision_id, "r1")
        self.assertIn("@rev-r1-", history[1].session.header.session_id)
        self.assertIn("@rev-r2-", history[2].session.header.session_id)
        self.assertIn("@rev-r1-", r1_session.header.session_id)
        self.assertIn("@rev-r2-", r2_session.header.session_id)

    def test_undo_redo_chain_and_branching_behavior(self):
        editor = SessionEditor(self._session())
        editor.apply({"type": "quantize", "grid": 4})
        editor.apply({"type": "transpose", "semitones": 1})
        editor.apply({"type": "velocity", "scale": 0.5, "offset": 0})
        self.assertEqual(len(editor.revision_history), 4)

        self.assertIsNotNone(editor.undo())
        self.assertIsNotNone(editor.undo())
        self.assertEqual(editor.revision_history[-1].revision_id, "r1")

        redone = editor.redo()
        self.assertIsNotNone(redone)
        self.assertEqual(editor.revision_history[-1].revision_id, "r2")

        branched = editor.apply({"type": "nudge", "delta_ticks": 1})
        self.assertIn("@rev-r3-", branched.header.session_id)
        self.assertIsNone(editor.redo(), "redo stack should clear after branching apply")


if __name__ == "__main__":
    unittest.main()
