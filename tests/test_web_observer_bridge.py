import asyncio
import json
import sys
import types
import unittest
from unittest import mock

try:
    from aiohttp import WSMsgType, web
except ModuleNotFoundError:  # pragma: no cover - local fallback for offline env
    aiohttp = types.ModuleType("aiohttp")

    class _WebSocketResponse:
        def __init__(self, *args, **kwargs):
            return

    class _Application(dict):
        def __init__(self):
            super().__init__()
            self.router = types.SimpleNamespace(add_get=lambda *_a, **_k: None)
            self.on_startup = []
            self.on_cleanup = []

    aiohttp.WSMsgType = types.SimpleNamespace(TEXT="TEXT", ERROR="ERROR")
    aiohttp.web = types.SimpleNamespace(
        Application=_Application,
        WebSocketResponse=_WebSocketResponse,
        Request=object,
        StreamResponse=object,
        Response=object,
        FileResponse=lambda *_a, **_k: None,
        json_response=lambda payload: payload,
        run_app=lambda *_a, **_k: None,
    )
    sys.modules["aiohttp"] = aiohttp
    from aiohttp import WSMsgType, web

from web.observer import DashboardServer, SnapshotBridge


class _FakeSnapshotClient:
    plans = []

    def __init__(self, *args, **kwargs):
        self._plan = _FakeSnapshotClient.plans.pop(0)

    def connect(self):
        action = self._plan.get("connect")
        if isinstance(action, Exception):
            raise action

    def recv_snapshot(self):
        item = self._plan["snapshots"].pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        return None


class _FakeMsg:
    def __init__(self, msg_type, data=""):
        self.type = msg_type
        self.data = data


class _FakeWS:
    def __init__(self, *args, **kwargs):
        self.prepared = False
        self.sent = []
        self.closed = False
        self._messages = []

    async def prepare(self, _request):
        self.prepared = True

    async def send_str(self, payload):
        self.sent.append(payload)

    def exception(self):
        return None

    def __aiter__(self):
        self._iter = iter(self._messages)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class SnapshotBridgeTest(unittest.TestCase):
    def test_reconnect_loop_recovers_after_initial_connect_failure(self):
        _FakeSnapshotClient.plans = [
            {"connect": OSError("down"), "snapshots": []},
            {"snapshots": [{"schema_version": 4, "transport": {"tick": 5}}, None]},
        ]

        bridge = SnapshotBridge("/tmp/test.sock", reconnect_delay_s=0.01)
        with mock.patch("web.observer.SnapshotClient", _FakeSnapshotClient):
            bridge.start()
            for _ in range(50):
                seq, snap, meta = bridge.current()
                if seq > 0:
                    break
                asyncio.run(asyncio.sleep(0.01))
            bridge.stop()

        self.assertGreaterEqual(seq, 1)
        self.assertEqual(snap["transport"]["tick"], 5)
        self.assertIn("last_update_age_ms", meta)


class DashboardServerTest(unittest.IsolatedAsyncioTestCase):
    async def test_healthz_reports_bridge_meta_and_throttle(self):
        server = DashboardServer(socket_path="/tmp/test.sock", host="127.0.0.1", port=0, max_broadcast_hz=12)
        server.bridge.current = lambda: (
            3,
            {"schema_version": 4, "transport": {"tick": 10}},
            {"connected": True, "reconnect_attempts": 2, "last_update_age_ms": 25.0},
        )
        resp = await server._healthz(mock.Mock())
        payload = getattr(resp, "text", None)
        if payload:
            data = json.loads(payload)
        else:
            data = resp
        self.assertTrue(data["ok"])
        self.assertEqual(data["seq"], 3)
        self.assertEqual(data["max_broadcast_hz"], 12.0)
        self.assertEqual(data["bridge"]["reconnect_attempts"], 2)

    async def test_ws_initial_payload_includes_metrics(self):
        server = DashboardServer(socket_path="/tmp/test.sock", host="127.0.0.1", port=0)
        server.bridge.current = lambda: (
            7,
            {"schema_version": 4, "transport": {"tick": 77}},
            {"connected": True, "reconnect_attempts": 0, "last_update_age_ms": 15.5},
        )

        fake_ws = _FakeWS()
        with mock.patch("web.observer.web.WebSocketResponse", return_value=fake_ws):
            await server._ws(mock.Mock())

        self.assertTrue(fake_ws.prepared)
        self.assertEqual(len(fake_ws.sent), 1)
        payload = json.loads(fake_ws.sent[0])
        self.assertEqual(payload["seq"], 7)
        self.assertEqual(payload["metrics"]["sequence_gap"], 0)
        self.assertEqual(payload["metrics"]["last_update_age_ms"], 15.5)

    async def test_broadcast_loop_evicts_stale_and_churned_websocket_clients(self):
        server = DashboardServer(socket_path="/tmp/test.sock", host="127.0.0.1", port=0, max_broadcast_hz=10)

        class _ChurnWS:
            def __init__(self, fail=False, closed=False):
                self.fail = fail
                self.closed = closed
                self.sent = []

            async def send_str(self, payload):
                if self.fail:
                    raise RuntimeError("disconnect")
                self.sent.append(payload)

        healthy = _ChurnWS(fail=False)
        churn = _ChurnWS(fail=True)
        closed = _ChurnWS(closed=True)
        server.clients = {healthy, churn, closed}

        seqs = iter(
            [
                (1, {"schema_version": 4, "transport": {"tick": 1}}, {"connected": True, "last_update_age_ms": 4.0}),
                (1, {"schema_version": 4, "transport": {"tick": 1}}, {"connected": True, "last_update_age_ms": 4.0}),
            ]
        )
        server.bridge.current = lambda: next(seqs)

        async def _sleep_and_stop(_):
            raise asyncio.CancelledError

        with mock.patch("web.observer.asyncio.sleep", side_effect=_sleep_and_stop):
            with self.assertRaises(asyncio.CancelledError):
                await server._broadcast_loop(web.Application())

        self.assertEqual(server.clients, {healthy})
        self.assertEqual(len(healthy.sent), 1)
        payload = json.loads(healthy.sent[0])
        self.assertEqual(payload["seq"], 1)
        self.assertEqual(payload["metrics"]["sequence_gap"], 0)


if __name__ == "__main__":
    unittest.main()
