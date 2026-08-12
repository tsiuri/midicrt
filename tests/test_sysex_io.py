import os
import tempfile
import time
import unittest

from web.sysex_io import SysexLibrary, split_messages


class SplitMessagesTest(unittest.TestCase):
    def test_single_message(self):
        raw = bytes([0xF0, 0x41, 0x10, 0xF7])
        self.assertEqual(split_messages(raw), [raw])

    def test_multi_message(self):
        m1 = bytes([0xF0, 0x41, 0xF7])
        m2 = bytes([0xF0, 0x42, 0x01, 0xF7])
        self.assertEqual(split_messages(m1 + m2), [m1, m2])

    def test_truncated_raises(self):
        with self.assertRaises(ValueError):
            split_messages(bytes([0xF0, 0x41, 0x10]))

    def test_garbage_between_frames_raises(self):
        raw = bytes([0xF0, 0x41, 0xF7, 0x00, 0xF0, 0x42, 0xF7])
        with self.assertRaises(ValueError) as ctx:
            split_messages(raw)
        self.assertIn("offset 3", str(ctx.exception))

    def test_empty_is_empty(self):
        self.assertEqual(split_messages(b""), [])


class SysexLibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = SysexLibrary(self.tmp.name)
        self.msg = bytes([0xF0, 0x41, 0x10, 0xF7])

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_list_read_roundtrip(self):
        self.lib.write("patch one.syx", self.msg)
        entries = self.lib.list()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "patch one.syx")
        self.assertEqual(entries[0]["messages"], 1)
        self.assertFalse(entries[0]["inbox"])
        self.assertEqual(self.lib.read("patch one.syx"), self.msg)

    def test_rename_delete(self):
        self.lib.write("a.syx", self.msg)
        self.lib.rename("a.syx", "b.syx")
        self.assertEqual([e["name"] for e in self.lib.list()], ["b.syx"])
        self.lib.delete("b.syx")
        self.assertEqual(self.lib.list(), [])

    def test_traversal_rejected(self):
        for bad in ("../evil.syx", "x/../../evil.syx", "/etc/passwd.syx", "noext"):
            with self.assertRaises(ValueError):
                self.lib.write(bad, self.msg)

    def test_inbox_coalescing_and_promote(self):
        f1 = self.lib.inbox_write(self.msg, now=1000.0)
        f2 = self.lib.inbox_write(self.msg, now=1001.0)   # <2s: same file
        f3 = self.lib.inbox_write(self.msg, now=1010.0)   # gap: new file
        self.assertEqual(f1, f2)
        self.assertNotEqual(f1, f3)
        inbox = [e for e in self.lib.list() if e["inbox"]]
        self.assertEqual(len(inbox), 2)
        two_msgs = [e for e in inbox if e["name"] == f1]
        self.assertEqual(two_msgs[0]["messages"], 2)
        self.lib.promote(f1, "kept.syx")
        names = [e["name"] for e in self.lib.list()]
        self.assertIn("kept.syx", names)
        self.assertNotIn(f1, names)


class _FakeMidoBackend:
    def __init__(self):
        self.sent = []
        self.sleeps = []

    def get_output_names(self):
        return ["Synth A 20:0", "Synth B 24:0"]

    def open_output(self, name):
        backend = self

        class _Out:
            def send(self, msg):
                backend.sent.append(bytes(msg.bytes()))

            def close(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.close()
                return False

        return _Out()


class SysexSenderTest(unittest.TestCase):
    def setUp(self):
        from web.sysex_io import SysexSender
        self.backend = _FakeMidoBackend()
        self.sender = SysexSender(backend=self.backend)
        self.two = bytes([0xF0, 0x41, 0xF7, 0xF0, 0x42, 0xF7])

    def test_sends_each_message_with_gaps(self):
        slept = []
        result = self.sender.send("Synth A 20:0", self.two, gap_ms=50, _sleep=slept.append)
        self.assertEqual(result, {"messages": 2, "bytes": 6})
        self.assertEqual(self.backend.sent,
                         [bytes([0xF0, 0x41, 0xF7]), bytes([0xF0, 0x42, 0xF7])])
        self.assertEqual(slept, [0.05])  # between messages only

    def test_unknown_port_rejected(self):
        with self.assertRaises(ValueError):
            self.sender.send("Nope 1:0", self.two, gap_ms=0)

    def test_invalid_data_rejected(self):
        with self.assertRaises(ValueError):
            self.sender.send("Synth A 20:0", b"\x00\x01", gap_ms=0)


class _FakeInBackend:
    def __init__(self, names):
        self.names = names
        self.callbacks = {}

    def get_input_names(self):
        return list(self.names)

    def open_input(self, name, callback):
        self.callbacks[name] = callback

        class _In:
            def close(self):
                pass

        return _In()


class SysexReceiverTest(unittest.TestCase):
    def test_saves_incoming_sysex_to_inbox(self):
        import mido
        from web.sysex_io import SysexReceiver
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        lib = SysexLibrary(tmp.name)
        backend = _FakeInBackend(["USB2.0-MIDI 24:0", "Midi Through 14:0"])
        rx = SysexReceiver(lib, patterns=["usb"], backend=backend, retry_s=0.05)
        rx.start()
        self.addCleanup(rx.stop)
        for _ in range(100):
            if rx.open_ports:
                break
            time.sleep(0.02)
        self.assertEqual(rx.open_ports, ["USB2.0-MIDI 24:0"])
        msg = mido.Message("sysex", data=[0x41, 0x10, 0x42, 0x12, 0x40, 0x00, 0x7F])
        backend.callbacks["USB2.0-MIDI 24:0"](msg)
        inbox = [e for e in lib.list() if e["inbox"]]
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0]["messages"], 1)

    def test_short_sysex_ignored(self):
        import mido
        from web.sysex_io import SysexReceiver
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        lib = SysexLibrary(tmp.name)
        backend = _FakeInBackend(["USB2.0-MIDI 24:0"])
        rx = SysexReceiver(lib, patterns=["usb"], backend=backend, retry_s=0.05)
        rx.start()
        self.addCleanup(rx.stop)
        for _ in range(100):
            if rx.open_ports:
                break
            time.sleep(0.02)
        backend.callbacks["USB2.0-MIDI 24:0"](mido.Message("sysex", data=[0x41]))
        self.assertEqual([e for e in lib.list() if e["inbox"]], [])


if __name__ == "__main__":
    unittest.main()
