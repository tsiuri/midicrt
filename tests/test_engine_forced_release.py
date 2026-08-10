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


class _BurstPort:
    """Fake port: yields one oversized burst, then nothing."""

    def __init__(self, messages):
        self._bursts = [list(messages)]

    def iter_pending(self):
        if self._bursts:
            return iter(self._bursts.pop(0))
        return iter(())


class BacklogShedTest(unittest.TestCase):
    def test_backlog_burst_sheds_clocks_but_keeps_notes(self):
        eng = MidiEngine()
        burst = [mido.Message("clock")] * (MidiEngine.BACKLOG_SHED_THRESHOLD + 20)
        burst.append(mido.Message("note_on", note=60, velocity=100, channel=2))
        burst.append(mido.Message("note_off", note=60, velocity=0, channel=2))
        burst.append(mido.Message("note_on", note=61, velocity=100, channel=2))
        port = _BurstPort(burst)

        calls = {"n": 0}

        def stop_flag():
            calls["n"] += 1
            return calls["n"] > 2

        eng.run_input_loop(port, stop_flag, sleep_s=0.0)

        self.assertEqual(eng.get_active_notes().get(2), {61})
        self.assertEqual(
            eng._shed_events_total, MidiEngine.BACKLOG_SHED_THRESHOLD + 20
        )

    def test_small_backlog_processes_everything(self):
        eng = MidiEngine()
        burst = [mido.Message("clock")] * 10
        burst.append(mido.Message("note_on", note=60, velocity=100, channel=0))
        port = _BurstPort(burst)

        calls = {"n": 0}

        def stop_flag():
            calls["n"] += 1
            return calls["n"] > 2

        eng.run_input_loop(port, stop_flag, sleep_s=0.0)

        self.assertEqual(eng.get_active_notes().get(0), {60})
        self.assertEqual(eng._shed_events_total, 0)


if __name__ == "__main__":
    unittest.main()
