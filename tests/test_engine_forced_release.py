import pathlib
import sys
import unittest

import mido

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from engine.core import MidiEngine


def _hold(eng, channel, notes):
    for note in notes:
        eng.ingest(mido.Message("note_on", note=note, velocity=100, channel=channel))


class ForcedReleaseTest(unittest.TestCase):
    def test_request_release_channel_clears_only_that_channel(self):
        eng = MidiEngine()
        _hold(eng, 4, [60, 64])
        _hold(eng, 9, [40])
        self.assertEqual(eng.get_active_notes()[4], {60, 64})

        eng.request_release(4)
        eng._drain_forced_releases()

        actives = eng.get_active_notes()
        self.assertFalse(actives.get(4))
        self.assertEqual(actives.get(9), {40})

    def test_request_release_all_clears_everything(self):
        eng = MidiEngine()
        _hold(eng, 0, [60])
        _hold(eng, 3, [61, 62])

        eng.request_release()
        eng._drain_forced_releases()

        for notes in eng.get_active_notes().values():
            self.assertFalse(notes)

    def test_cc123_queues_release_for_channel(self):
        eng = MidiEngine()
        _hold(eng, 2, [55, 57])

        eng.ingest(mido.Message("control_change", control=123, value=0, channel=2))
        eng._drain_forced_releases()

        self.assertFalse(eng.get_active_notes().get(2))

    def test_drain_without_requests_is_noop(self):
        eng = MidiEngine()
        _hold(eng, 1, [70])
        eng._drain_forced_releases()
        self.assertEqual(eng.get_active_notes().get(1), {70})


if __name__ == "__main__":
    unittest.main()
