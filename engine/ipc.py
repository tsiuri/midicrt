from __future__ import annotations

import json
import os
import socket
import threading
import time
from copy import deepcopy
from typing import Any, Callable

PROTOCOL_VERSION = 1
ENVELOPE_SNAPSHOT = "snapshot"
ENVELOPE_COMMAND = "command"
ENVELOPE_ACK = "ack"
ENVELOPE_ERROR = "error"


def _normalize_deep_research_metadata(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Attach stable deep-research metadata without breaking optional semantics."""
    if not isinstance(snapshot, dict):
        return snapshot

    raw = snapshot.get("deep_research")
    if not isinstance(raw, dict):
        return snapshot

    deep_research = deepcopy(raw)
    produced_at = float(deep_research.get("produced_at", deep_research.get("timestamp", 0.0)))
    transport = snapshot.get("transport") if isinstance(snapshot.get("transport"), dict) else {}
    source_tick = int(deep_research.get("source_tick", transport.get("tick", 0)))
    lag_ms = float(deep_research.get("lag_ms", max(0.0, (time.time() - produced_at) * 1000.0) if produced_at > 0 else 0.0))

    deep_research["produced_at"] = produced_at
    deep_research["source_tick"] = source_tick
    deep_research["lag_ms"] = lag_ms
    deep_research["stale"] = bool(deep_research.get("stale", False) or lag_ms > 2000.0)

    normalized = dict(snapshot)
    normalized["deep_research"] = deep_research
    return normalized


def make_envelope(envelope_type: str, payload: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    env: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "type": envelope_type,
        "payload": payload if isinstance(payload, dict) else {},
    }
    env.update(extra)
    return env


class SnapshotPublisher:
    """Publish snapshots and accept JSON commands via Unix domain socket."""

    def __init__(
        self,
        socket_path: str,
        enabled: bool = True,
        publish_hz: float = 20.0,
        command_handler: Callable[[str, dict[str, Any] | None], tuple[bool, dict[str, Any]]] | None = None,
    ) -> None:
        self.socket_path = socket_path
        self.enabled = enabled
        self.publish_hz = max(0.1, float(publish_hz))
        self.command_handler = command_handler
        self._server: socket.socket | None = None
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()
        self._accept_thread: threading.Thread | None = None
        self._running = False
        self._last_publish = 0.0

    def set_command_handler(
        self,
        command_handler: Callable[[str, dict[str, Any] | None], tuple[bool, dict[str, Any]]] | None,
    ) -> None:
        self.command_handler = command_handler

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

        envelope = make_envelope(ENVELOPE_SNAPSHOT, _normalize_deep_research_metadata(snapshot))
        payload = (json.dumps(envelope, separators=(",", ":")) + "\n").encode("utf-8")
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

    def _send(self, client: socket.socket, envelope: dict[str, Any]) -> None:
        client.sendall((json.dumps(envelope, separators=(",", ":")) + "\n").encode("utf-8"))

    def _handle_command(self, envelope: dict[str, Any]) -> dict[str, Any]:
        request_id = envelope.get("request_id")
        cmd = envelope.get("command")
        payload = envelope.get("payload")
        if not isinstance(cmd, str) or not cmd:
            return make_envelope(
                ENVELOPE_ERROR,
                {"code": "invalid-command", "message": "missing command"},
                request_id=request_id,
            )
        if self.command_handler is None:
            return make_envelope(
                ENVELOPE_ERROR,
                {"code": "unsupported", "message": "command handler unavailable"},
                request_id=request_id,
                command=cmd,
            )
        try:
            ok, data = self.command_handler(cmd, payload if isinstance(payload, dict) else None)
        except Exception as exc:
            return make_envelope(
                ENVELOPE_ERROR,
                {"code": "exception", "message": str(exc)},
                request_id=request_id,
                command=cmd,
            )
        if ok:
            return make_envelope(ENVELOPE_ACK, data, request_id=request_id, command=cmd)
        return make_envelope(ENVELOPE_ERROR, data, request_id=request_id, command=cmd)

    def _client_loop(self, client: socket.socket) -> None:
        try:
            with client.makefile("r", encoding="utf-8", newline="\n") as reader:
                while self._running:
                    line = reader.readline()
                    if not line:
                        break
                    try:
                        env = json.loads(line)
                    except Exception:
                        try:
                            self._send(client, make_envelope(ENVELOPE_ERROR, {"code": "invalid-json", "message": "malformed JSON"}))
                        except OSError:
                            break
                        continue
                    if not isinstance(env, dict) or env.get("type") != ENVELOPE_COMMAND:
                        continue
                    try:
                        self._send(client, self._handle_command(env))
                    except OSError:
                        break
        except OSError:
            pass
        finally:
            with self._lock:
                self._clients = [c for c in self._clients if c is not client]
            try:
                client.close()
            except OSError:
                pass

    def _accept_loop(self) -> None:
        assert self._server is not None
        while self._running:
            try:
                client, _ = self._server.accept()
                client.setblocking(True)
                with self._lock:
                    self._clients.append(client)
                threading.Thread(target=self._client_loop, args=(client,), daemon=True, name="snapshot-ipc-client").start()
            except TimeoutError:
                continue
            except OSError:
                if self._running:
                    time.sleep(0.05)
