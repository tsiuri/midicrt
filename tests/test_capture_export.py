import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

try:
    import mido
except ModuleNotFoundError:  # pragma: no cover - local fallback for offline test envs
    mido = types.ModuleType("mido")

    class _BaseMessage:
        def __init__(self, mtype, time=0, **kwargs):
            self.type = mtype
            self.time = int(time)
            for key, value in kwargs.items():
                setattr(self, key, value)

        @property
        def is_meta(self):
            return False

        def copy(self, **kwargs):
            data = dict(vars(self))
            data.update(kwargs)
            mtype = data.pop("type")
            return _BaseMessage(mtype, **data)

        def bytes(self):
            status = 0x90 if self.type == "note_on" else 0x80
            status += int(getattr(self, "channel", 0))
            return [status, int(getattr(self, "note", 0)), int(getattr(self, "velocity", 0))]

    class _MetaMessage(_BaseMessage):
        @property
        def is_meta(self):
            return True

    class _MidiTrack(list):
        pass

    class _MidiFile:
        def __init__(self, path=None, ticks_per_beat=480):
            self.ticks_per_beat = ticks_per_beat
            self.tracks = []
            if path:
                payload = json.loads(pathlib.Path(path).read_text())
                self.ticks_per_beat = payload["ticks_per_beat"]
                self.tracks = [_MidiTrack(_deserialize_track(payload["track"]))]

        def save(self, path):
            track = self.tracks[0] if self.tracks else []
            payload = {
                "ticks_per_beat": self.ticks_per_beat,
                "track": [_serialize_msg(msg) for msg in track],
            }
            pathlib.Path(path).write_text(json.dumps(payload))

    def _serialize_msg(msg):
        return {
            "meta": msg.is_meta,
            "type": msg.type,
            "attrs": {k: v for k, v in vars(msg).items() if k != "type"},
        }

    def _deserialize_track(items):
        out = []
        for item in items:
            attrs = dict(item["attrs"])
            if item["meta"]:
                out.append(_MetaMessage(item["type"], **attrs))
            else:
                out.append(_BaseMessage(item["type"], **attrs))
        return out

    def _from_bytes(data):
        status = int(data[0])
        channel = status & 0x0F
        kind = "note_on" if (status & 0xF0) == 0x90 else "note_off"
        return _BaseMessage(kind, channel=channel, note=int(data[1]), velocity=int(data[2]), time=0)

    mido.Message = _BaseMessage
    mido.Message.from_bytes = staticmethod(_from_bytes)
    mido.MetaMessage = _MetaMessage
    mido.MidiTrack = _MidiTrack
    mido.MidiFile = _MidiFile
    mido.bpm2tempo = lambda bpm: int(round(60_000_000 / float(bpm)))
    sys.modules["mido"] = mido

from engine.core import MidiEngine


FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "capture_replay.json"


class CaptureExportTest(unittest.TestCase):
    def _engine(self, output_dir):
        eng = MidiEngine()
        eng.configure_capture(
            {
                "bars_to_keep": 16,
                "dump_bars": 2,
                "output_dir": output_dir,
                "file_prefix": "testcap",
                "default_bpm": 120.0,
            }
        )
        return eng

    def test_bar_window_selection_uses_recent_bars(self):
        with tempfile.TemporaryDirectory() as tmp:
            eng = self._engine(tmp)
            with eng._lock:
                eng.state.tick_counter = 320
                eng.state.bpm = 120.0
            eng._capture_events.extend(
                [
                    {"timestamp": 190.0, "beat_tick": 50, "kind": "note_on", "msg": mido.Message("note_on", note=60, velocity=90, channel=0)},
                    {"timestamp": 199.0, "beat_tick": 200, "kind": "note_on", "msg": mido.Message("note_on", note=64, velocity=90, channel=0)},
                    {"timestamp": 199.5, "beat_tick": 220, "kind": "note_off", "msg": mido.Message("note_off", note=64, velocity=0, channel=0)},
                ]
            )

            with mock.patch("engine.core.time.strftime", return_value="20260101-120000"), mock.patch(
                "engine.core.time.time", return_value=200.0
            ):
                ok, _message, path = eng.capture_recent_to_file(bars=1, trigger="unit")

            self.assertTrue(ok)
            midi = mido.MidiFile(path)
            msg_notes = [m for m in midi.tracks[0] if not m.is_meta]
            self.assertEqual([m.note for m in msg_notes], [64, 64])

    def test_deterministic_export_from_fixture_replay(self):
        payload = json.loads(FIXTURE.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            eng = self._engine(tmp)

            for ev in payload["capture_events"]:
                msg = mido.Message.from_bytes(ev["bytes"])
                eng._capture_events.append(
                    {
                        "timestamp": ev["timestamp"],
                        "beat_tick": ev["beat_tick"],
                        "kind": ev["kind"],
                        "msg": msg,
                    }
                )
            with eng._lock:
                eng.state.tick_counter = payload["tick_now"]
                eng.state.bpm = payload["bpm"]

            with mock.patch("engine.core.time.strftime", return_value="20260101-120000"), mock.patch(
                "engine.core.time.time", return_value=payload["now"]
            ):
                ok, _message, path = eng.capture_recent_to_file(bars=payload["bars"], trigger="replay")

            self.assertTrue(ok)
            self.assertTrue(os.path.exists(path))
            midi = mido.MidiFile(path)
            actual = [
                {
                    "type": m.type,
                    "channel": getattr(m, "channel", None),
                    "note": getattr(m, "note", None),
                    "velocity": getattr(m, "velocity", None),
                    "time": m.time,
                }
                for m in midi.tracks[0]
                if not m.is_meta
            ]
            self.assertEqual(actual, payload["expected_messages"])


if __name__ == "__main__":
    unittest.main()
