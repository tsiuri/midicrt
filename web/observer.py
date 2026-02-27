from __future__ import annotations

import contextlib
import argparse
import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from ui.client import SnapshotClient, normalize_snapshot

_LOG = logging.getLogger("midicrt.web.observer")


class SnapshotBridge:
    """Background bridge from engine IPC snapshots to websocket clients."""

    def __init__(self, socket_path: str, reconnect_delay_s: float = 1.0) -> None:
        self.socket_path = socket_path
        self.reconnect_delay_s = max(0.1, float(reconnect_delay_s))
        self._lock = threading.Lock()
        self._latest: dict[str, Any] | None = None
        self._seq = 0
        self._last_update_monotonic = 0.0
        self._connected = False
        self._reconnect_attempts = 0
        self._last_error = ""
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="web-snapshot-bridge")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def current(self) -> tuple[int, dict[str, Any] | None, dict[str, Any]]:
        with self._lock:
            age_ms = max(0.0, (time.monotonic() - self._last_update_monotonic) * 1000.0) if self._last_update_monotonic else None
            meta = {
                "connected": self._connected,
                "reconnect_attempts": self._reconnect_attempts,
                "reconnect_delay_s": self.reconnect_delay_s,
                "last_error": self._last_error,
                "last_update_age_ms": age_ms,
            }
            return self._seq, self._latest, meta

    def _publish(self, snapshot: dict[str, Any]) -> None:
        normalized = normalize_snapshot(snapshot)
        with self._lock:
            self._latest = normalized
            self._seq += 1
            self._last_update_monotonic = time.monotonic()

    def _run_loop(self) -> None:
        while self._running:
            client = SnapshotClient(socket_path=self.socket_path, enabled=True, timeout_s=1.0)
            try:
                _LOG.info("connecting to snapshot socket: %s", self.socket_path)
                client.connect()
                with self._lock:
                    self._connected = True
                    self._last_error = ""
                while self._running:
                    snapshot = client.recv_snapshot()
                    if snapshot is None:
                        break
                    self._publish(snapshot)
            except OSError as exc:
                with self._lock:
                    self._last_error = str(exc)
                _LOG.warning("snapshot bridge connection error: %s", exc)
            except Exception:
                with self._lock:
                    self._last_error = "unexpected snapshot bridge error"
                _LOG.exception("unexpected snapshot bridge error")
            finally:
                with self._lock:
                    self._connected = False
                    self._reconnect_attempts += 1
                client.close()
            if self._running:
                time.sleep(self.reconnect_delay_s)


class DashboardServer:
    def __init__(
        self,
        socket_path: str,
        host: str,
        port: int,
        max_broadcast_hz: float = 20.0,
        client_queue_size: int = 8,
    ) -> None:
        self.socket_path = socket_path
        self.host = host
        self.port = int(port)
        self.max_broadcast_hz = max(1.0, float(max_broadcast_hz))
        self.client_queue_size = max(1, int(client_queue_size))
        self.bridge = SnapshotBridge(socket_path=socket_path)
        self.clients: set[web.WebSocketResponse] = set()
        self.client_queues: dict[web.WebSocketResponse, asyncio.Queue[tuple[int, str]]] = {}
        self.client_send_tasks: dict[web.WebSocketResponse, asyncio.Task[None]] = {}
        self._queue_dropped = 0
        self._queue_coalesced = 0
        self.static_dir = Path(__file__).with_name("static")

    def _telemetry(self) -> dict[str, int]:
        return {
            "queue_dropped": self._queue_dropped,
            "queue_coalesced": self._queue_coalesced,
        }

    async def _index(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(self.static_dir / "index.html")

    async def _healthz(self, request: web.Request) -> web.Response:
        seq, snapshot, meta = self.bridge.current()
        return web.json_response(
            {
                "ok": True,
                "seq": seq,
                "has_snapshot": bool(snapshot),
                "bridge": meta,
                "max_broadcast_hz": self.max_broadcast_hz,
                "client_queue_size": self.client_queue_size,
                "telemetry": self._telemetry(),
            }
        )

    @staticmethod
    def _payload_for(
        seq: int,
        snapshot: dict[str, Any],
        bridge_meta: dict[str, Any],
        last_seq: int | None,
        telemetry: dict[str, int] | None = None,
    ) -> str:
        sequence_gap = 0 if last_seq is None else max(0, seq - last_seq - 1)
        deep = snapshot.get("deep_research") if isinstance(snapshot.get("deep_research"), dict) else None
        deep_meta = {
            "available": bool(deep),
            "produced_at": deep.get("produced_at") if deep else None,
            "source_tick": deep.get("source_tick") if deep else None,
            "stale": bool(deep.get("stale", False)) if deep else None,
            "lag_ms": deep.get("lag_ms") if deep else None,
        }
        payload = {
            "seq": seq,
            "snapshot": snapshot,
            "bridge": bridge_meta,
            "metrics": {
                "sequence_gap": sequence_gap,
                "last_update_age_ms": bridge_meta.get("last_update_age_ms"),
                "fanout": telemetry or {"queue_dropped": 0, "queue_coalesced": 0},
            },
            "deep_research": deep_meta,
        }
        return json.dumps(payload, separators=(",", ":"))

    async def _ws(self, request: web.Request) -> web.StreamResponse:
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)
        self.clients.add(ws)
        queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue(maxsize=self.client_queue_size)
        sender_task = asyncio.create_task(self._client_sender(ws, queue))
        self.client_queues[ws] = queue
        self.client_send_tasks[ws] = sender_task

        seq, snapshot, bridge_meta = self.bridge.current()
        if snapshot is not None:
            payload = self._payload_for(seq, snapshot, bridge_meta, last_seq=None, telemetry=self._telemetry())
            await ws.send_str(payload)

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT and msg.data.strip().lower() == "ping":
                    await ws.send_str("pong")
                elif msg.type == WSMsgType.ERROR:
                    _LOG.warning("websocket error: %s", ws.exception())
        finally:
            self._drop_client(ws)
            sender_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sender_task
        return ws

    @staticmethod
    async def _client_sender(ws: web.WebSocketResponse, queue: asyncio.Queue[tuple[int, str]]) -> None:
        while True:
            _seq, payload = await queue.get()
            await ws.send_str(payload)

    async def _enqueue_payload(self, ws: web.WebSocketResponse, queue: asyncio.Queue[tuple[int, str]], seq: int, payload: str) -> None:
        if ws.closed:
            raise ConnectionError("websocket closed")
        try:
            queue.put_nowait((seq, payload))
            return
        except asyncio.QueueFull:
            self._queue_dropped += 1
        try:
            queue.get_nowait()
            self._queue_coalesced += 1
        except asyncio.QueueEmpty:
            pass
        queue.put_nowait((seq, payload))

    def _drop_client(self, ws: web.WebSocketResponse) -> None:
        self.clients.discard(ws)
        self.client_queues.pop(ws, None)
        task = self.client_send_tasks.pop(ws, None)
        if task is not None:
            task.cancel()

    async def _broadcast_loop(self, app: web.Application) -> None:
        last_seq = -1
        client_last_seq: dict[web.WebSocketResponse, int] = {}
        interval_s = 1.0 / self.max_broadcast_hz
        while True:
            seq, snapshot, bridge_meta = self.bridge.current()
            if snapshot is not None and seq != last_seq and self.clients:
                stale: list[web.WebSocketResponse] = []
                for ws in self.clients:
                    if getattr(ws, "closed", False):
                        stale.append(ws)
                        continue
                    try:
                        queue = self.client_queues.get(ws)
                        if queue is None:
                            stale.append(ws)
                            continue
                        payload = self._payload_for(seq, snapshot, bridge_meta, last_seq=client_last_seq.get(ws), telemetry=self._telemetry())
                        await self._enqueue_payload(ws, queue, seq, payload)
                        client_last_seq[ws] = seq
                    except Exception:
                        stale.append(ws)
                for ws in stale:
                    self._drop_client(ws)
                    client_last_seq.pop(ws, None)
                last_seq = seq
            await asyncio.sleep(interval_s)

    async def _on_startup(self, app: web.Application) -> None:
        self.bridge.start()
        app["broadcast_task"] = asyncio.create_task(self._broadcast_loop(app))

    async def _on_cleanup(self, app: web.Application) -> None:
        task = app.get("broadcast_task")
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.bridge.stop()

    def build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_get("/healthz", self._healthz)
        app.router.add_get("/ws", self._ws)
        app.on_startup.append(self._on_startup)
        app.on_cleanup.append(self._on_cleanup)
        return app

    def run(self) -> None:
        app = self.build_app()
        web.run_app(app, host=self.host, port=self.port)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="midicrt read-only web observer")
    parser.add_argument("--socket-path", default="/tmp/midicrt.sock", help="Unix socket path for engine snapshots")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (default: loopback only)")
    parser.add_argument("--port", default=8765, type=int, help="HTTP/WebSocket bind port")
    parser.add_argument("--max-broadcast-hz", default=20.0, type=float, help="Maximum websocket broadcast rate (samples latest snapshot)")
    parser.add_argument("--client-queue-size", default=8, type=int, help="Per-client outbound queue size before coalescing")
    parser.add_argument("--log-level", default="INFO", help="Python logging level")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    DashboardServer(
        socket_path=args.socket_path,
        host=args.host,
        port=args.port,
        max_broadcast_hz=args.max_broadcast_hz,
        client_queue_size=args.client_queue_size,
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
