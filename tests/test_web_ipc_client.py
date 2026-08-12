import json
import os
import socket
import tempfile
import threading
import time
import unittest

from web.ipc_client import send_command


class _FakeAppSocket:
    """Speaks the engine IPC protocol: replies to a command envelope after
    first emitting a snapshot line (clients must skip those)."""

    def __init__(self, response_builder):
        self._dir = tempfile.mkdtemp()
        self.path = os.path.join(self._dir, "sock")
        self._response_builder = response_builder
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.path)
        self._srv.listen(1)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        client, _ = self._srv.accept()
        with client, client.makefile("r", encoding="utf-8", newline="\n") as reader:
            line = reader.readline()
            req = json.loads(line)
            noise = {"protocol_version": 1, "type": "snapshot", "payload": {"x": 1}}
            client.sendall((json.dumps(noise) + "\n").encode())
            resp = self._response_builder(req)
            client.sendall((json.dumps(resp) + "\n").encode())

    def close(self):
        self._srv.close()


class _SnapshotOnlySocket:
    """A server that continuously sends snapshot envelopes but never replies
    to commands. Used to test deadline enforcement."""

    def __init__(self):
        self._dir = tempfile.mkdtemp()
        self.path = os.path.join(self._dir, "sock")
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.path)
        self._srv.listen(1)
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        try:
            client, _ = self._srv.accept()
            with client, client.makefile("r", encoding="utf-8", newline="\n") as reader:
                # Read the command but never reply
                line = reader.readline()
                # Continuously send snapshots every 0.05s
                while self._running:
                    noise = {"protocol_version": 1, "type": "snapshot", "payload": {"x": 1}}
                    try:
                        client.sendall((json.dumps(noise) + "\n").encode())
                    except (BrokenPipeError, ConnectionResetError):
                        break
                    time.sleep(0.05)
        except Exception:
            pass

    def close(self):
        self._running = False
        self._srv.close()


class SendCommandTest(unittest.TestCase):
    def test_ack_roundtrip_skips_snapshot_noise(self):
        def build(req):
            assert req["type"] == "command" and req["command"] == "capture_recent"
            return {"protocol_version": 1, "type": "ack",
                    "request_id": req["request_id"], "command": req["command"],
                    "payload": {"path": "/tmp/x.mid"}}

        fake = _FakeAppSocket(build)
        try:
            ok, data = send_command("capture_recent", {}, socket_path=fake.path)
        finally:
            fake.close()
        self.assertTrue(ok)
        self.assertEqual(data.get("path"), "/tmp/x.mid")

    def test_error_envelope_returns_false(self):
        def build(req):
            return {"protocol_version": 1, "type": "error",
                    "request_id": req["request_id"], "command": req["command"],
                    "payload": {"code": "invalid-args", "message": "nope"}}

        fake = _FakeAppSocket(build)
        try:
            ok, data = send_command("set_config", {"bad": 1}, socket_path=fake.path)
        finally:
            fake.close()
        self.assertFalse(ok)
        self.assertIn("nope", data.get("error", ""))

    def test_connect_failure_returns_false(self):
        ok, data = send_command("capture_recent", {}, socket_path="/tmp/does-not-exist-9x.sock",
                                timeout_s=0.5)
        self.assertFalse(ok)
        self.assertIn("error", data)

    def test_total_deadline_enforced_with_continuous_snapshots(self):
        """Verify that timeout_s enforces total round-trip deadline,
        not per-read deadline. A server that streams snapshots every 0.05s
        should not keep the client alive past timeout_s."""
        fake = _SnapshotOnlySocket()
        try:
            start = time.monotonic()
            ok, data = send_command("test_cmd", {}, socket_path=fake.path, timeout_s=0.5)
            elapsed = time.monotonic() - start
            self.assertFalse(ok)
            self.assertIn("timeout", data.get("error", "").lower())
            # Should time out within ~0.5s + a small margin for system overhead
            self.assertLess(elapsed, 2.0, f"Timed out in {elapsed:.2f}s, expected ~0.5s")
        finally:
            fake.close()


if __name__ == "__main__":
    unittest.main()
