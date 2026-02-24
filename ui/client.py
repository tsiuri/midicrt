from __future__ import annotations

import json
import socket
from typing import Any, Callable


class SnapshotClient:
    """Read newline-delimited snapshots from an engine IPC socket.

    If IPC is disabled, it can fall back to polling an in-process engine.
    """

    def __init__(
        self,
        socket_path: str,
        enabled: bool = True,
        engine_snapshot_fn: Callable[[], dict[str, Any]] | None = None,
        timeout_s: float = 0.25,
    ) -> None:
        self.socket_path = socket_path
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
            return normalize_snapshot(json.loads(line))
        if self.engine_snapshot_fn:
            return normalize_snapshot(self.engine_snapshot_fn())
        return None


def normalize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return a schema snapshot across legacy and v2 payload shapes."""
    if not isinstance(snapshot, dict):
        return {}
    if "transport" in snapshot and "schema_version" in snapshot:
        return snapshot
    nested = snapshot.get("schema")
    if isinstance(nested, dict):
        return nested
    return snapshot


def render_snapshot(snapshot: dict[str, Any], cols: int = 80) -> list[str]:
    snapshot = normalize_snapshot(snapshot)
    transport = snapshot.get("transport", {})
    status = snapshot.get("status_text", "")
    channels = snapshot.get("channels", [])
    mods = snapshot.get("module_outputs", {})
    views = snapshot.get("views", {}) if isinstance(snapshot.get("views"), dict) else {}

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
    ]

    return [ln[:cols].ljust(cols) for ln in lines]
