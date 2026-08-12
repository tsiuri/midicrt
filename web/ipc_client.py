"""Blocking client for the engine's IPC command channel.

The snapshot socket streams `snapshot` envelopes to every client; a
command client must send one `command` envelope and then read lines,
skipping snapshots, until the `ack`/`error` envelope with its own
request_id arrives. Callers on the aiohttp loop run this via
`asyncio.to_thread`.
"""
from __future__ import annotations

import json
import socket
import time
import uuid


def send_command(command: str, payload: dict | None = None,
                 socket_path: str = "/tmp/midicrt.sock",
                 timeout_s: float = 3.0) -> tuple[bool, dict]:
    request_id = uuid.uuid4().hex
    envelope = {
        "protocol_version": 1,
        "type": "command",
        "command": command,
        "payload": payload if isinstance(payload, dict) else {},
        "request_id": request_id,
    }
    deadline = time.monotonic() + timeout_s
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(socket_path)
            sock.sendall((json.dumps(envelope) + "\n").encode("utf-8"))
            with sock.makefile("r", encoding="utf-8", newline="\n") as reader:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False, {"error": "timeout waiting for reply"}
                    sock.settimeout(remaining)
                    line = reader.readline()
                    if not line:
                        return False, {"error": "connection closed before reply"}
                    try:
                        env = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(env, dict):
                        continue
                    if env.get("type") == "snapshot":
                        continue
                    if env.get("request_id") != request_id:
                        continue
                    body = env.get("payload") if isinstance(env.get("payload"), dict) else {}
                    if env.get("type") == "ack":
                        return True, body
                    message = body.get("message") or body.get("code") or "command failed"
                    return False, {"error": str(message)}
    except OSError as exc:
        return False, {"error": f"midicrt app unreachable: {exc}"}
