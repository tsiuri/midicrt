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

    def current(self) -> tuple[int, dict[str, Any] | None]:
        with self._lock:
            return self._seq, self._latest

    def _publish(self, snapshot: dict[str, Any]) -> None:
        normalized = normalize_snapshot(snapshot)
        with self._lock:
            self._latest = normalized
            self._seq += 1

    def _run_loop(self) -> None:
        while self._running:
            client = SnapshotClient(socket_path=self.socket_path, enabled=True, timeout_s=1.0)
            try:
                _LOG.info("connecting to snapshot socket: %s", self.socket_path)
                client.connect()
                while self._running:
                    snapshot = client.recv_snapshot()
                    if snapshot is None:
                        break
                    self._publish(snapshot)
            except OSError as exc:
                _LOG.warning("snapshot bridge connection error: %s", exc)
            except Exception:
                _LOG.exception("unexpected snapshot bridge error")
            finally:
                client.close()
            if self._running:
                time.sleep(self.reconnect_delay_s)


class DashboardServer:
    def __init__(self, socket_path: str, host: str, port: int) -> None:
        self.socket_path = socket_path
        self.host = host
        self.port = int(port)
        self.bridge = SnapshotBridge(socket_path=socket_path)
        self.clients: set[web.WebSocketResponse] = set()
        self.static_dir = Path(__file__).with_name("static")

    async def _index(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(self.static_dir / "index.html")

    async def _healthz(self, request: web.Request) -> web.Response:
        seq, snapshot = self.bridge.current()
        return web.json_response({"ok": True, "seq": seq, "has_snapshot": bool(snapshot)})

    async def _ws(self, request: web.Request) -> web.StreamResponse:
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)
        self.clients.add(ws)

        seq, snapshot = self.bridge.current()
        if snapshot is not None:
            await ws.send_str(json.dumps({"seq": seq, "snapshot": snapshot}, separators=(",", ":")))

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT and msg.data.strip().lower() == "ping":
                    await ws.send_str("pong")
                elif msg.type == WSMsgType.ERROR:
                    _LOG.warning("websocket error: %s", ws.exception())
        finally:
            self.clients.discard(ws)
        return ws

    async def _broadcast_loop(self, app: web.Application) -> None:
        last_seq = -1
        while True:
            seq, snapshot = self.bridge.current()
            if snapshot is not None and seq != last_seq and self.clients:
                payload = json.dumps({"seq": seq, "snapshot": snapshot}, separators=(",", ":"))
                stale: list[web.WebSocketResponse] = []
                for ws in self.clients:
                    try:
                        await ws.send_str(payload)
                    except Exception:
                        stale.append(ws)
                for ws in stale:
                    self.clients.discard(ws)
                last_seq = seq
            await asyncio.sleep(0.05)

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
    parser.add_argument("--log-level", default="INFO", help="Python logging level")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    DashboardServer(socket_path=args.socket_path, host=args.host, port=args.port).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
