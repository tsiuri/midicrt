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
    def _json_response(payload, status=200):
        return types.SimpleNamespace(text=json.dumps(payload), status=status)

    aiohttp.web = types.SimpleNamespace(
        Application=_Application,
        WebSocketResponse=_WebSocketResponse,
        Request=object,
        StreamResponse=object,
        Response=object,
        FileResponse=lambda *_a, **_k: None,
        json_response=_json_response,
        run_app=lambda *_a, **_k: None,
        middleware=lambda fn: fn,
    )
    sys.modules["aiohttp"] = aiohttp
    from aiohttp import WSMsgType, web

from web.observer import DashboardServer, SnapshotBridge


class _FakeSnapshotClient:
    plans = []

    def __init__(self, *args, **kwargs):
        self._plan = _FakeSnapshotClient.plans.pop(0) if _FakeSnapshotClient.plans else {"snapshots": [None]}

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

    async def send_json(self, payload):
        self.sent.append(json.dumps(payload))

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

        bridge = SnapshotBridge("/tmp/test.sock", reconnect_backoff_min_s=0.01, reconnect_backoff_max_s=0.01, reconnect_backoff_base_s=0.01, reconnect_backoff_jitter_s=0.0)
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

        bridge = SnapshotBridge("/tmp/test.sock", reconnect_backoff_min_s=0.01, reconnect_backoff_max_s=0.01, reconnect_backoff_base_s=0.01, reconnect_backoff_jitter_s=0.0)
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
        self.assertGreaterEqual(meta["successful_reconnects"], 1)
        self.assertEqual(meta["consecutive_failures"], 0)



    def test_reconnect_classifies_no_data_and_socket_errors(self):
        _FakeSnapshotClient.plans = [
            {"connect": OSError("down"), "snapshots": []},
            {"snapshots": [None]},
            {"snapshots": [{"schema_version": 4, "transport": {"tick": 15}}, None]},
        ]

        bridge = SnapshotBridge("/tmp/test.sock", reconnect_backoff_min_s=0.01, reconnect_backoff_max_s=0.01, reconnect_backoff_base_s=0.01, reconnect_backoff_jitter_s=0.0)
        with mock.patch("web.observer.SnapshotClient", _FakeSnapshotClient):
            bridge.start()
            for _ in range(120):
                seq, _snap, _meta = bridge.current()
                if seq >= 1:
                    break
                asyncio.run(asyncio.sleep(0.01))
            bridge.stop()

        _seq, _snap, meta = bridge.current()
        self.assertEqual(meta["last_error_code"], "no-data")
        self.assertGreaterEqual(meta["total_failures"], 2)
        self.assertEqual(meta["consecutive_failures"], 0)
        self.assertIsNotNone(meta["last_successful_connect_ts"])

    def test_reconnect_backoff_grows_with_failures_and_is_capped(self):
        _FakeSnapshotClient.plans = [
            {"connect": OSError("down-1"), "snapshots": []},
            {"connect": OSError("down-2"), "snapshots": []},
            {"connect": OSError("down-3"), "snapshots": []},
            {"snapshots": [{"schema_version": 4, "transport": {"tick": 21}}, None]},
        ]

        bridge = SnapshotBridge(
            "/tmp/test.sock",
            reconnect_backoff_min_s=0.01,
            reconnect_backoff_max_s=0.03,
            reconnect_backoff_base_s=0.01,
            reconnect_backoff_jitter_s=0.0,
        )
        sleeps = []

        def _fake_sleep(duration):
            sleeps.append(duration)

        with mock.patch("web.observer.SnapshotClient", _FakeSnapshotClient), mock.patch("web.observer.time.sleep", side_effect=_fake_sleep), mock.patch("web.observer.random.uniform", return_value=0.0):
            bridge.start()
            for _ in range(80):
                seq, _snap, _meta = bridge.current()
                if seq >= 1:
                    break
                asyncio.run(asyncio.sleep(0.005))
            bridge.stop()

        self.assertGreaterEqual(seq, 1)
        self.assertGreaterEqual(len(sleeps), 3)
        self.assertEqual(sleeps[:3], [0.05, 0.05, 0.05])

class DashboardServerTest(unittest.IsolatedAsyncioTestCase):
    async def test_healthz_reports_bridge_meta_and_throttle(self):
        server = DashboardServer(socket_path="/tmp/test.sock", host="127.0.0.1", port=0, max_broadcast_hz=12)
        server.bridge.current = lambda: (
            3,
            {"schema_version": 4, "transport": {"tick": 10}},
            {"connected": True, "consecutive_failures": 0, "total_failures": 2, "successful_reconnects": 1, "last_successful_connect_ts": 1700000000.0, "last_error_code": "", "last_update_age_ms": 25.0},
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
        self.assertEqual(data["bridge"]["total_failures"], 2)
        self.assertIn("telemetry", data)
        self.assertIn("schema_health", data)
        self.assertEqual(data["read_only"]["mutation_endpoints"], [])
        self.assertEqual(data["read_only"]["command_execution_paths"], [])
        self.assertEqual(data["read_only"]["mode"], "strict-read-only")
        self.assertEqual(data["read_only"]["allowed_http_methods"], ["GET"])
        self.assertEqual(data["read_only"]["bounded_polling"]["max_broadcast_hz"], 12.0)

    async def test_ws_initial_payload_includes_metrics(self):
        server = DashboardServer(socket_path="/tmp/test.sock", host="127.0.0.1", port=0)
        server.bridge.current = lambda: (
            7,
            {"schema_version": 4, "transport": {"tick": 77}},
            {"connected": True, "consecutive_failures": 0, "total_failures": 0, "successful_reconnects": 0, "last_successful_connect_ts": 1700000000.0, "last_error_code": "", "last_update_age_ms": 15.5},
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
        self.assertEqual(payload["schema_health"]["latest_snapshot_version"], 4)
        self.assertEqual(payload["read_only"]["mutation_endpoints"], [])
        self.assertEqual(payload["read_only"]["command_execution_paths"], [])

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
                (1, {"schema_version": 4, "transport": {"tick": 1}}, {"connected": True, "consecutive_failures": 0, "total_failures": 0, "successful_reconnects": 0, "last_successful_connect_ts": 1700000000.0, "last_error_code": "", "last_update_age_ms": 4.0}),
                (1, {"schema_version": 4, "transport": {"tick": 1}}, {"connected": True, "consecutive_failures": 0, "total_failures": 0, "successful_reconnects": 0, "last_successful_connect_ts": 1700000000.0, "last_error_code": "", "last_update_age_ms": 4.0}),
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
        server = DashboardServer(socket_path="/tmp/test.sock", host="127.0.0.1", port=0, max_broadcast_hz=20)
        payload = server._payload_for(
            9,
            {
                "schema_version": 4,
                "compat_mode": "native",
                "transport": {"tick": 9},
                "deep_research": {"produced_at": 1234.5, "source_tick": 4, "stale": True, "lag_ms": 987.0},
                "diagnostics": {"modules": {"deep_research": {"metrics": {"modules": {"deepresearch": {"over_budget_count": 2, "skipped_due_degradation": 1, "last_runtime_ms": 11.2}}}}}},
                "views": {
                    "tempo_quality": {"jitter_ms": 2.4, "drift_ppm": -0.5},
                    "microtiming": {"title": "Microtiming", "total_samples": 12, "buckets": ["early", "late"]},
                    "motif": {"found": True, "pattern": "+4 -2", "count": 3, "window": 2},
                    "capture_status": {
                        "armed": True,
                        "state": "capturing",
                        "buffer_fill": 14,
                        "buffer_capacity": 64,
                        "commit_state": "dirty",
                        "last_commit": "2026-02-27T12:00:00Z",
                    },
                },
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
        self.assertEqual(data["schema_health"]["latest_snapshot_version"], 4)
        self.assertEqual(data["snapshot"]["compat_mode"], "native")
        self.assertEqual(data["observer_views"]["tempo_quality"]["jitter_ms"], 2.4)
        self.assertEqual(data["observer_views"]["tempo_quality"]["drift_ppm"], -0.5)
        self.assertEqual(data["observer_views"]["motif"]["pattern"], "+4 -2")
        self.assertEqual(data["observer_views"]["capture_status"]["buffer_fill"], 14)
        self.assertEqual(data["read_only"]["bounded_stream_rate_hz"], 20.0)
        self.assertEqual(data["read_only"]["mode"], "strict-read-only")
        self.assertEqual(data["metrics"]["module_health"]["warnings"][0]["module"], "deepresearch")
        self.assertEqual(data["observer_views"]["module_health"]["warnings"][0]["over_budget_count"], 2)

    async def test_read_only_method_guard_rejects_mutation_methods(self):
        server = DashboardServer(socket_path="/tmp/test.sock", host="127.0.0.1", port=0)
        request = types.SimpleNamespace(method="POST")
        response = await server._read_only_method_guard(request, mock.AsyncMock())
        payload = json.loads(response.text)
        self.assertEqual(response.status, 405)
        self.assertEqual(payload["error"], "read-only observer: mutation methods are disabled")
        self.assertEqual(payload["read_only"]["allowed_http_methods"], ["GET"])

    async def test_ws_rejects_non_ping_inbound_actions(self):
        server = DashboardServer(socket_path="/tmp/test.sock", host="127.0.0.1", port=0)
        server.bridge.current = lambda: (1, {"schema_version": 4, "transport": {"tick": 1}}, {"connected": True, "last_update_age_ms": 1.0})
        fake_ws = _FakeWS()
        fake_ws._messages = [types.SimpleNamespace(type=WSMsgType.TEXT, data="run panic")]  # non-ping action
        with mock.patch("web.observer.web.WebSocketResponse", return_value=fake_ws):
            await server._ws(mock.Mock())
        self.assertEqual(len(fake_ws.sent), 2)
        self.assertEqual(json.loads(fake_ws.sent[1])["error"], "read-only websocket: inbound command actions are disabled")

    def test_payload_schema_surface_stability(self):
        server = DashboardServer(socket_path="/tmp/test.sock", host="127.0.0.1", port=0)
        payload = server._payload_for(
            4,
            {"schema_version": 4, "compat_mode": "legacy-v3", "transport": {"tick": 8}},
            {"connected": True, "last_update_age_ms": 8.0},
            last_seq=3,
        )
        data = json.loads(payload)
        expected_top_keys = {
            "seq",
            "snapshot",
            "bridge",
            "metrics",
            "deep_research",
            "schema_health",
            "observer_views",
            "read_only",
        }
        self.assertEqual(set(data.keys()), expected_top_keys)
        self.assertIn("mode", data["read_only"])
        self.assertIn("allowed_http_methods", data["read_only"])
        self.assertIn("websocket_rejected_actions", data["read_only"])

    def test_payload_includes_schema_health_fallback_counter_for_legacy_envelopes(self):
        server = DashboardServer(socket_path="/tmp/test.sock", host="127.0.0.1", port=0, max_broadcast_hz=15)
        with mock.patch("web.observer.get_normalization_stats", return_value={"fallbacks": 3}):
            payload = server._payload_for(
                11,
                {"schema_version": 2, "transport": {"tick": 5}},
                {"connected": True, "last_update_age_ms": 33.0},
                last_seq=10,
                telemetry={"queue_dropped": 0, "queue_coalesced": 0},
            )
        data = json.loads(payload)
        self.assertEqual(data["schema_health"]["normalization_fallbacks"], 3)
        self.assertEqual(data["schema_health"]["ipc_freshness_age_ms"], 33.0)
        self.assertEqual(data["read_only"]["bounded_stream_rate_hz"], 15.0)


if __name__ == "__main__":
    unittest.main()
