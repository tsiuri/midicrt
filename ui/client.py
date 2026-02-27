from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any, Callable

from engine.ipc import ENVELOPE_ACK, ENVELOPE_COMMAND, ENVELOPE_ERROR, ENVELOPE_SNAPSHOT, make_envelope


_NORMALIZATION_LOCK = threading.Lock()
_NORMALIZATION_FALLBACKS = 0


def _record_normalization_fallback() -> None:
    global _NORMALIZATION_FALLBACKS
    with _NORMALIZATION_LOCK:
        _NORMALIZATION_FALLBACKS += 1


def get_normalization_stats() -> dict[str, int]:
    with _NORMALIZATION_LOCK:
        return {"fallbacks": _NORMALIZATION_FALLBACKS}


def reset_normalization_stats() -> None:
    global _NORMALIZATION_FALLBACKS
    with _NORMALIZATION_LOCK:
        _NORMALIZATION_FALLBACKS = 0


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


def _merge_optional_metadata(snapshot: dict[str, Any], source: dict[str, Any] | None) -> dict[str, Any]:
    """Merge optional metadata for older envelopes that stored fields outside schema payload."""
    if not isinstance(snapshot, dict):
        return {}
    if not isinstance(source, dict):
        return snapshot

    merged = dict(snapshot)
    if not isinstance(merged.get("deep_research"), dict) and isinstance(source.get("deep_research"), dict):
        merged["deep_research"] = source["deep_research"]

    compatibility_fields = ("module_health", "retrospective_capture")
    for field in compatibility_fields:
        if not isinstance(merged.get(field), dict) and isinstance(source.get(field), dict):
            merged[field] = source[field]
    return merged


def _extract_snapshot_schema(snapshot: dict[str, Any]) -> tuple[dict[str, Any], bool, dict[str, Any] | None]:
    """Extract schema payload from mixed-version envelopes.

    Returns: (schema_payload, used_fallback_path, metadata_source)
    """
    if not isinstance(snapshot, dict):
        return {}, False, None

    if "transport" in snapshot and "schema_version" in snapshot:
        return snapshot, False, None

    envelope_payload = snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else None
    if snapshot.get("type") == ENVELOPE_SNAPSHOT and envelope_payload:
        if "transport" in envelope_payload and "schema_version" in envelope_payload:
            return envelope_payload, False, envelope_payload
        nested = envelope_payload.get("schema")
        if isinstance(nested, dict):
            return nested, True, envelope_payload

    nested = snapshot.get("schema")
    if isinstance(nested, dict):
        return nested, True, snapshot

    if envelope_payload:
        nested = envelope_payload.get("schema")
        if isinstance(nested, dict):
            return nested, True, envelope_payload
        if "transport" in envelope_payload and "schema_version" in envelope_payload:
            return envelope_payload, True, snapshot

    return snapshot, False, None


def normalize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return a schema snapshot across modern and legacy envelope payload shapes."""
    if not isinstance(snapshot, dict):
        return {}

    normalized, used_fallback, metadata_source = _extract_snapshot_schema(snapshot)
    if used_fallback:
        _record_normalization_fallback()
    return _merge_optional_metadata(normalized, metadata_source)




def _deep_research_status(snapshot: dict[str, Any]) -> str:
    payload = snapshot.get("deep_research")
    if not isinstance(payload, dict) or not payload:
        return "deep_research unavailable"
    result = payload.get("result")
    if not isinstance(result, dict) or not result:
        return "deep_research enabled (no result)"
    keys = ",".join(sorted(result.keys())[:3])
    return f"deep_research result[{keys}]"

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
        _deep_research_status(snapshot),
    ]

    return [ln[:cols].ljust(cols) for ln in lines]
