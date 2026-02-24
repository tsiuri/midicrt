from __future__ import annotations

import json
import os
import socket
import threading
import time
from typing import Any


class SnapshotPublisher:
    """Publish state snapshots to local UI clients via Unix domain socket."""

    def __init__(
        self,
        socket_path: str,
        enabled: bool = True,
        publish_hz: float = 20.0,
    ) -> None:
        self.socket_path = socket_path
        self.enabled = enabled
        self.publish_hz = max(0.1, float(publish_hz))
        self._server: socket.socket | None = None
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()
        self._accept_thread: threading.Thread | None = None
        self._running = False
        self._last_publish = 0.0

    def start(self) -> None:
        if not self.enabled or self._running:
            return
        os.makedirs(os.path.dirname(self.socket_path) or ".", exist_ok=True)
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self.socket_path)
        srv.listen(8)
        srv.settimeout(0.5)
        self._server = srv
        self._running = True
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True, name="snapshot-ipc-accept")
        self._accept_thread.start()

    def stop(self) -> None:
        self._running = False
        with self._lock:
            for client in self._clients:
                try:
                    client.close()
                except OSError:
                    pass
            self._clients.clear()
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass

    def publish(self, snapshot: dict[str, Any], force: bool = False) -> bool:
        if not self.enabled or not self._running:
            return False
        interval = 1.0 / self.publish_hz
        now = time.monotonic()
        if not force and now - self._last_publish < interval:
            return False
        self._last_publish = now

        payload = (json.dumps(snapshot, separators=(",", ":")) + "\n").encode("utf-8")
        stale: list[socket.socket] = []
        with self._lock:
            for client in self._clients:
                try:
                    client.sendall(payload)
                except OSError:
                    stale.append(client)
            if stale:
                self._clients = [c for c in self._clients if c not in stale]
                for client in stale:
                    try:
                        client.close()
                    except OSError:
                        pass
        return True

    def _accept_loop(self) -> None:
        assert self._server is not None
        while self._running:
            try:
                client, _ = self._server.accept()
                client.setblocking(True)
                with self._lock:
                    self._clients.append(client)
            except TimeoutError:
                continue
            except OSError:
                if self._running:
                    time.sleep(0.05)
