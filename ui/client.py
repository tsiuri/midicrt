from __future__ import annotations

import json
import socket
import time
from typing import Any, Callable

from engine.ipc import ENVELOPE_ACK, ENVELOPE_COMMAND, ENVELOPE_ERROR, ENVELOPE_SNAPSHOT, make_envelope


class SnapshotClient:
    """Read snapshots and optionally send commands over engine IPC sockets."""

    def __init__(
        self,
        socket_path: str,
        enabled: bool = True,
        engine_snapshot_fn: Callable[[], dict[str, Any]] | None = None,
        timeout_s: float = 0.25,
        command_socket_path: str | None = None,
    ) -> None:
        self.socket_path = socket_path
        self.command_socket_path = command_socket_path or socket_path
        self.enabled = enabled
        self.engine_snapshot_fn = engine_snapshot_fn
        self.timeout_s = timeout_s
        self._sock: socket.socket | None = None
        self._file = None

    def connect(self) -> None:
        if not self.enabled:
            return
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout_s)
        sock.connect(self.socket_path)
        self._sock = sock
        self._file = sock.makefile("r", encoding="utf-8", newline="\n")

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def recv_snapshot(self) -> dict[str, Any] | None:
        if self.enabled and self._file is not None:
            try:
                line = self._file.readline()
            except TimeoutError:
                return None
            if not line:
                return None
            msg = json.loads(line)
            if isinstance(msg, dict) and msg.get("type") == ENVELOPE_SNAPSHOT:
                return normalize_snapshot(msg.get("payload", {}))
            return normalize_snapshot(msg)
        if self.engine_snapshot_fn:
            return normalize_snapshot(self.engine_snapshot_fn())
        return None

    def send_command(
        self,
        command: str,
        payload: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        if not self.enabled:
            return False, {"code": "ipc-disabled", "message": "IPC disabled"}
        timeout = self.timeout_s if timeout_s is None else float(timeout_s)
        request_id = f"{int(time.time() * 1000)}"
        request = make_envelope(
            ENVELOPE_COMMAND,
            payload if isinstance(payload, dict) else {},
            request_id=request_id,
            command=str(command or ""),
        )
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(self.command_socket_path)
            sock.sendall((json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8"))
            with sock.makefile("r", encoding="utf-8", newline="\n") as reader:
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    line = reader.readline()
                    if not line:
                        break
                    env = json.loads(line)
                    if not isinstance(env, dict):
                        continue
                    if env.get("request_id") != request_id:
                        continue
                    etype = env.get("type")
                    data = env.get("payload", {})
                    if etype == ENVELOPE_ACK:
                        return True, data if isinstance(data, dict) else {}
                    if etype == ENVELOPE_ERROR:
                        return False, data if isinstance(data, dict) else {"code": "error", "message": "command failed"}
        except TimeoutError:
            return False, {"code": "timeout", "message": "command timed out"}
        except OSError as exc:
            return False, {"code": "io-error", "message": str(exc)}
        finally:
            sock.close()
        return False, {"code": "no-reply", "message": "no command reply received"}


def normalize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return a schema snapshot across modern, legacy, and envelope payload shapes."""
    if not isinstance(snapshot, dict):
        return {}

    if snapshot.get("type") == ENVELOPE_SNAPSHOT and isinstance(snapshot.get("payload"), dict):
        snapshot = snapshot["payload"]

    if "transport" in snapshot and "schema_version" in snapshot:
        return snapshot

    nested = snapshot.get("schema")
    if isinstance(nested, dict):
        return nested

    payload = snapshot.get("payload")
    if isinstance(payload, dict):
        nested = payload.get("schema")
        if isinstance(nested, dict):
            return nested
        if "transport" in payload and "schema_version" in payload:
            return payload

    return snapshot


def render_snapshot(snapshot: dict[str, Any], cols: int = 80) -> list[str]:
    snapshot = normalize_snapshot(snapshot)
    transport = snapshot.get("transport", {})
    status = snapshot.get("status_text", "")
    channels = snapshot.get("channels", [])
    mods = snapshot.get("module_outputs", {})
    views = snapshot.get("views", {}) if isinstance(snapshot.get("views"), dict) else {}
    diagnostics = snapshot.get("diagnostics", {}) if isinstance(snapshot.get("diagnostics"), dict) else {}
    sched = diagnostics.get("scheduler", {}) if isinstance(diagnostics.get("scheduler"), dict) else {}
    overloaded = sched.get("overloaded_modules", []) if isinstance(sched.get("overloaded_modules"), list) else []

    lines = [
        f"schema v{snapshot.get('schema_version', '?')}  t={snapshot.get('timestamp', 0):.3f}",
        (
            "transport "
            f"tick={transport.get('tick', 0)} "
            f"bar={transport.get('bar', 0)} "
            f"run={transport.get('running', False)} "
            f"bpm={transport.get('bpm', 0.0):.2f}"
        ),
        f"status {status}",
        "channels "
        + ", ".join(
            f"ch{ch.get('channel', '?')}:[{','.join(str(n) for n in ch.get('active_notes', []))}]" for ch in channels
        ),
        "modules " + ", ".join(sorted(mods.keys())),
        "views " + ", ".join(sorted(views.keys())) if views else "views (none)",
        ("scheduler overload=" + ",".join(overloaded[:3])) if overloaded else "scheduler ok",
    ]

    return [ln[:cols].ljust(cols) for ln in lines]
