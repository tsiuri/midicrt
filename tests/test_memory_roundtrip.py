import json
import pathlib
import tempfile
import unittest

from engine.memory import midi_io, storage
from engine.memory.session_model import TempoSegment, TimeSignatureSegment, build_session_model

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "memory_same_tick_stream.json"


class MemoryRoundtripTest(unittest.TestCase):
    def _build_fixture_session(self):
        payload = json.loads(FIXTURE.read_text())
        header = payload["session"]
        session = build_session_model(
            session_id=header["session_id"],
            start_tick=header["start_tick"],
            bpm=header["bpm"],
            ppqn=header["ppqn"],
            tempo_segments=[TempoSegment(**seg) for seg in header["tempo_segments"]],
            time_signature_segments=[TimeSignatureSegment(**seg) for seg in header["time_signature_segments"]],
        )
        for event in payload["events"]:
            session.append_normalized_event(**event)
        session.header.stop_tick = header["stop_tick"]
        return session

    def _event_tuple(self, ev):
        return (
            ev.kind,
            ev.tick,
            ev.channel,
            ev.note,
            ev.velocity,
            ev.control,
            ev.value,
            ev.program,
            ev.pitch,
            ev.pressure,
        )

    def test_export_import_reimport_preserves_canonical_event_stream(self):
        source = self._build_fixture_session()
        with tempfile.TemporaryDirectory() as tmp:
            first_path = pathlib.Path(tmp) / "first.mid"
            second_path = pathlib.Path(tmp) / "second.mid"

            self.assertEqual(midi_io.export_session_midi(source, str(first_path)), str(first_path))
            first_import = midi_io.import_midi_file(str(first_path), session_id="import-a")
            self.assertIsNotNone(first_import)

            self.assertEqual(midi_io.export_session_midi(first_import, str(second_path)), str(second_path))
            second_import = midi_io.import_midi_file(str(second_path), session_id="import-b")
            self.assertIsNotNone(second_import)

            first_events = [self._event_tuple(ev) for ev in first_import.events]
            second_events = [self._event_tuple(ev) for ev in second_import.events]
            self.assertEqual(first_events, second_events)

    def test_storage_roundtrip_preserves_event_and_metadata_segments(self):
        source = self._build_fixture_session()
        with tempfile.TemporaryDirectory() as tmp:
            saved_path = storage.save_session(tmp, source)
            loaded = storage.load_session(saved_path)
            self.assertIsNotNone(loaded)

            self.assertEqual(
                [(seg.start_tick, seg.bpm) for seg in loaded.header.tempo_segments],
                [(0, 120.0), (8, 96.0)],
            )
            self.assertEqual(
                [(seg.start_tick, seg.numerator, seg.denominator) for seg in loaded.header.time_signature_segments],
                [(0, 4, 4), (12, 3, 4)],
            )
            self.assertEqual(
                [self._event_tuple(ev) for ev in source.events],
                [self._event_tuple(ev) for ev in loaded.events],
            )


if __name__ == "__main__":
    unittest.main()
