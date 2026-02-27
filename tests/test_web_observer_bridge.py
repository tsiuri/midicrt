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

    def test_reconnect_resubscribes_after_midstream_disconnect(self):
        _FakeSnapshotClient.plans = [
            {"snapshots": [{"schema_version": 4, "transport": {"tick": 10}}, OSError("dropped")]},
            {"snapshots": [{"schema_version": 4, "transport": {"tick": 11}}, None]},
        ]

        bridge = SnapshotBridge("/tmp/test.sock", reconnect_delay_s=0.01)
        with mock.patch("web.observer.SnapshotClient", _FakeSnapshotClient):
            bridge.start()
            for _ in range(100):
                seq, snap, meta = bridge.current()
                if seq >= 2:
                    break
                asyncio.run(asyncio.sleep(0.01))
            bridge.stop()

        self.assertGreaterEqual(seq, 2)
        self.assertEqual(snap["transport"]["tick"], 11)
        self.assertGreaterEqual(meta["reconnect_attempts"], 1)


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
        self.assertIn("telemetry", data)

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
        server.client_queues = {healthy: asyncio.Queue(maxsize=2), churn: asyncio.Queue(maxsize=2), closed: asyncio.Queue(maxsize=2)}

        async def _enqueue(ws, _queue, _seq, payload):
            await ws.send_str(payload)

        server._enqueue_payload = _enqueue

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

    async def test_queue_backpressure_coalesces_and_tracks_telemetry(self):
        server = DashboardServer(socket_path="/tmp/test.sock", host="127.0.0.1", port=0, client_queue_size=1)
        ws = _FakeWS()
        queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue(maxsize=1)

        await server._enqueue_payload(ws, queue, 1, "first")
        await server._enqueue_payload(ws, queue, 2, "second")

        self.assertEqual(server._queue_dropped, 1)
        self.assertEqual(server._queue_coalesced, 1)
        self.assertEqual(queue.qsize(), 1)
        seq, payload = queue.get_nowait()
        self.assertEqual(seq, 2)
        self.assertEqual(payload, "second")

    def test_payload_reports_stale_deep_research_metadata(self):
        payload = DashboardServer._payload_for(
            9,
            {
                "schema_version": 4,
                "transport": {"tick": 9},
                "deep_research": {"produced_at": 1234.5, "source_tick": 4, "stale": True, "lag_ms": 987.0},
            },
            {"connected": True, "last_update_age_ms": 20.0},
            last_seq=7,
            telemetry={"queue_dropped": 2, "queue_coalesced": 3},
        )
        data = json.loads(payload)
        self.assertTrue(data["deep_research"]["stale"])
        self.assertEqual(data["deep_research"]["lag_ms"], 987.0)
        self.assertEqual(data["metrics"]["fanout"]["queue_dropped"], 2)
        self.assertEqual(data["metrics"]["sequence_gap"], 1)


if __name__ == "__main__":
    unittest.main()
