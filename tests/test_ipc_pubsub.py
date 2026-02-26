import json
import socket
import tempfile
import time
import unittest
from unittest import mock

from engine.ipc import ENVELOPE_COMMAND, ENVELOPE_ERROR, SnapshotPublisher
from ui.client import SnapshotClient


class IpcPubSubTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.socket_path = f"{self.tmp.name}/ipc.sock"
        self.publisher = SnapshotPublisher(self.socket_path, publish_hz=50.0)
        self.publisher.start()

    def tearDown(self):
        self.publisher.stop()
        self.tmp.cleanup()

    def _connect(self):
        deadline = time.time() + 1.0
        while True:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            try:
                sock.connect(self.socket_path)
                return sock
            except (BlockingIOError, FileNotFoundError, ConnectionRefusedError):
                sock.close()
                if time.time() >= deadline:
                    raise
                time.sleep(0.01)

    def test_connect_disconnect_churn(self):
        clients = []
        for _ in range(20):
            sock = self._connect()
            clients.append(sock)
            sock.close()
        time.sleep(0.1)
        self.publisher.publish({"schema_version": 3, "transport": {}}, force=True)
        time.sleep(0.1)
        self.assertEqual(self.publisher._clients, [])

    def test_stale_client_cleanup_on_publish(self):
        live = self._connect()
        stale = self._connect()
        stale.close()
        time.sleep(0.05)

        sent = self.publisher.publish({"schema_version": 3, "transport": {}}, force=True)
        self.assertTrue(sent)
        time.sleep(0.05)
        self.assertEqual(len(self.publisher._clients), 1)

        live.close()

    def test_publish_is_throttled_without_force(self):
        self.publisher._last_publish = 0.0
        with mock.patch("engine.ipc.time.monotonic", side_effect=[1.00, 1.01, 1.03]):
            first = self.publisher.publish({"schema_version": 3, "transport": {}}, force=False)
            second = self.publisher.publish({"schema_version": 3, "transport": {}}, force=False)
            third = self.publisher.publish({"schema_version": 3, "transport": {}}, force=False)
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertTrue(third)

    def test_malformed_payload_returns_error_envelope(self):
        client = self._connect()
        try:
            client.sendall(b"{not-json}\n")
            data = client.recv(4096).decode("utf-8")
            env = json.loads(data.strip())
            self.assertEqual(env.get("type"), ENVELOPE_ERROR)
            self.assertEqual(env.get("payload", {}).get("code"), "invalid-json")

            malformed_cmd = {"type": ENVELOPE_COMMAND, "command": "noop", "payload": "bad"}
            client.sendall((json.dumps(malformed_cmd) + "\n").encode("utf-8"))
            data2 = client.recv(4096).decode("utf-8")
            env2 = json.loads(data2.strip())
            self.assertEqual(env2.get("type"), ENVELOPE_ERROR)
            self.assertEqual(env2.get("payload", {}).get("code"), "unsupported")
        finally:
            client.close()

    def test_unsupported_command_returns_structured_error(self):
        client = self._connect()
        try:
            cmd = {"type": ENVELOPE_COMMAND, "request_id": "req-1", "command": "set_page", "payload": {"page": 1}}
            client.sendall((json.dumps(cmd) + "\n").encode("utf-8"))
            data = client.recv(4096).decode("utf-8")
            env = json.loads(data.strip())
            self.assertEqual(env.get("type"), ENVELOPE_ERROR)
            self.assertEqual(env.get("request_id"), "req-1")
            self.assertEqual(env.get("payload", {}).get("code"), "unsupported")
        finally:
            client.close()

    def test_capture_command_ack_contains_path(self):
        self.publisher.set_command_handler(
            lambda command, payload: (
                True,
                {
                    "message": "capture saved",
                    "path": "/tmp/captures/recent.mid",
                    "bars": payload.get("bars") if isinstance(payload, dict) else None,
                },
            )
            if command == "capture_recent"
            else (False, {"code": "unknown-command", "message": command})
        )
        client = SnapshotClient(self.socket_path, timeout_s=0.25)
        ok, result = client.send_command("capture_recent", {"bars": 2})

        self.assertTrue(ok)
        self.assertEqual(result.get("path"), "/tmp/captures/recent.mid")
        self.assertEqual(result.get("bars"), 2)

    def test_command_timeout_returns_timeout_code(self):
        def _slow_handler(_command, _payload):
            time.sleep(0.2)
            return True, {"message": "late"}

        self.publisher.set_command_handler(_slow_handler)
        client = SnapshotClient(self.socket_path, timeout_s=0.05)
        ok, result = client.send_command("capture_recent", {"bars": 1})

        self.assertFalse(ok)
        self.assertEqual(result.get("code"), "timeout")


if __name__ == "__main__":
    unittest.main()
