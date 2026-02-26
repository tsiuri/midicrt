import asyncio
import json
import sys
import types
import unittest
from unittest import mock

try:
    from aiohttp import web
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
    from aiohttp import web

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
                seq, snap = bridge.current()
                if seq > 0:
                    break
                asyncio.run(asyncio.sleep(0.01))
            bridge.stop()

        self.assertGreaterEqual(seq, 1)
        self.assertEqual(snap["transport"]["tick"], 5)


class DashboardServerTest(unittest.IsolatedAsyncioTestCase):
    async def test_broadcast_loop_evicts_churned_websocket_clients(self):
        server = DashboardServer(socket_path="/tmp/test.sock", host="127.0.0.1", port=0)

        class _ChurnWS:
            def __init__(self, fail=False):
                self.fail = fail
                self.sent = []

            async def send_str(self, payload):
                if self.fail:
                    raise RuntimeError("disconnect")
                self.sent.append(payload)

        healthy = _ChurnWS(fail=False)
        churn = _ChurnWS(fail=True)
        server.clients = {healthy, churn}

        seqs = iter([(1, {"schema_version": 4, "transport": {"tick": 1}}), (1, {"schema_version": 4, "transport": {"tick": 1}})])
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


if __name__ == "__main__":
    unittest.main()
