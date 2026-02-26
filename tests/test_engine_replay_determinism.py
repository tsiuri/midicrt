import json
import pathlib
import sys
import types
import unittest
from unittest import mock

try:
    import mido
except ModuleNotFoundError:  # pragma: no cover - local fallback for offline env
    mido = types.ModuleType("mido")

    class _Message:
        def __init__(self, mtype, **kwargs):
            self.type = mtype
            for key, value in kwargs.items():
                setattr(self, key, value)

        @staticmethod
        def from_bytes(data):
            status = int(data[0])
            channel = status & 0x0F
            kind = "note_on" if (status & 0xF0) == 0x90 else "note_off"
            return _Message(kind, channel=channel, note=int(data[1]), velocity=int(data[2]))

    mido.Message = _Message
    sys.modules["mido"] = mido

from engine.core import MidiEngine


FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "capture_replay.json"


def _msg_from_bytes(data):
    from_bytes = getattr(mido.Message, "from_bytes", None)
    if callable(from_bytes):
        return from_bytes(data)
    status = int(data[0])
    channel = status & 0x0F
    kind = "note_on" if (status & 0xF0) == 0x90 else "note_off"
    return mido.Message(kind, channel=channel, note=int(data[1]), velocity=int(data[2]))



class _CountModule:
    name = "count"

    def __init__(self):
        self.note_on = 0
        self.note_off = 0

    def on_event(self, event):
        if event["kind"] == "note_on" and int(event.get("velocity", 0)) > 0:
            self.note_on += 1
        elif event["kind"] in {"note_off", "note_on"}:
            self.note_off += 1

    def on_clock(self, _snapshot):
        return None

    def get_outputs(self):
        return {"note_on": self.note_on, "note_off": self.note_off}


def _run_once(payload):
    module = _CountModule()
    eng = MidiEngine(modules=[module])
    for ev in payload["capture_events"]:
        msg = _msg_from_bytes(ev["bytes"])
        with mock.patch("engine.core.time.time", return_value=ev["timestamp"]):
            eng.ingest(msg)
    schema = eng.get_snapshot()["schema"]
    module_out = schema.get("module_outputs", {}).get("count", {})
    return {
        "transport": {
            "tick": schema["transport"].get("tick"),
            "bar": schema["transport"].get("bar"),
            "running": schema["transport"].get("running"),
            "bpm": schema["transport"].get("bpm"),
            "meter_estimate": schema["transport"].get("meter_estimate"),
        },
        "module_outputs": {
            "count": {"note_on": module_out.get("note_on"), "note_off": module_out.get("note_off")}
        },
        "channels": schema["channels"],
    }


class EngineReplayDeterminismTest(unittest.TestCase):
    def test_fixture_replay_outputs_and_transport_are_deterministic(self):
        payload = json.loads(FIXTURE.read_text())
        first = _run_once(payload)
        second = _run_once(payload)

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
