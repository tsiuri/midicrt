"""Sysex file + MIDI mechanics for the web management surface.

No aiohttp imports here: this module is plain files + mido so it can be
unit-tested without a server and reused by anything else.
"""
from __future__ import annotations

import os
import re
import threading
import time

_NAME_RE = re.compile(r"^[A-Za-z0-9._ -]+$")
_INBOX_GAP_S = 2.0


def split_messages(data: bytes) -> list[bytes]:
    """Split a .syx blob into F0..F7 frames; reject bytes outside frames."""
    messages: list[bytes] = []
    i = 0
    n = len(data)
    while i < n:
        if data[i] != 0xF0:
            raise ValueError(f"byte outside sysex frame at offset {i}")
        try:
            end = data.index(0xF7, i)
        except ValueError:
            raise ValueError(f"unterminated sysex frame starting at offset {i}") from None
        messages.append(data[i:end + 1])
        i = end + 1
    return messages


class SysexLibrary:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.inbox_dir = os.path.join(self.root, "inbox")
        os.makedirs(self.inbox_dir, exist_ok=True)
        self._last_inbox_file: str | None = None
        self._last_inbox_ts = 0.0
        self._inbox_lock = threading.Lock()

    # -- name handling -------------------------------------------------
    def _resolve(self, name: str) -> str:
        rel = name[len("inbox/"):] if name.startswith("inbox/") else name
        if len(rel) > 100:
            raise ValueError("name too long")
        if not rel.endswith(".syx"):
            raise ValueError("name must end with .syx")
        if not _NAME_RE.match(rel) or rel.startswith("."):
            raise ValueError("invalid characters in name")
        base = self.inbox_dir if name.startswith("inbox/") else self.root
        path = os.path.abspath(os.path.join(base, rel))
        if os.path.dirname(path) != base:
            raise ValueError("path escapes library root")
        return path

    # -- operations ----------------------------------------------------
    def list(self) -> list[dict]:
        entries = []
        for inbox, base in ((False, self.root), (True, self.inbox_dir)):
            for fn in sorted(os.listdir(base)):
                path = os.path.join(base, fn)
                if not fn.endswith(".syx") or not os.path.isfile(path):
                    continue
                with open(path, "rb") as f:
                    data = f.read()
                try:
                    count = len(split_messages(data))
                except ValueError:
                    count = -1
                entries.append({
                    "name": (f"inbox/{fn}" if inbox else fn),
                    "bytes": len(data),
                    "messages": count,
                    "mtime": os.path.getmtime(path),
                    "inbox": inbox,
                })
        return entries

    def read(self, name: str) -> bytes:
        with open(self._resolve(name), "rb") as f:
            return f.read()

    def write(self, name: str, data: bytes) -> None:
        with open(self._resolve(name), "wb") as f:
            f.write(data)

    def rename(self, old: str, new: str) -> None:
        os.rename(self._resolve(old), self._resolve(new))

    def delete(self, name: str) -> None:
        os.remove(self._resolve(name))

    def promote(self, inbox_name: str, new_name: str) -> None:
        if not inbox_name.startswith("inbox/"):
            raise ValueError("promote source must be in inbox/")
        if new_name.startswith("inbox/"):
            raise ValueError("promote target must be in the library root")
        os.rename(self._resolve(inbox_name), self._resolve(new_name))

    def inbox_write(self, data: bytes, now: float | None = None) -> str:
        with self._inbox_lock:
            now = time.time() if now is None else now
            if self._last_inbox_file is None or (now - self._last_inbox_ts) >= _INBOX_GAP_S:
                stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
                self._last_inbox_file = f"inbox/{stamp}.syx"
            self._last_inbox_ts = now
            with open(self._resolve(self._last_inbox_file), "ab") as f:
                f.write(data)
            return self._last_inbox_file


class SysexSender:
    """Send .syx blobs out a chosen MIDI output, paced between messages.

    Old synths overrun when multi-message dumps are blasted back-to-back;
    `gap_ms` (clamped 0..2000, default chosen by the caller) sleeps between
    messages. Blocking — callers on an event loop use asyncio.to_thread.
    """

    def __init__(self, backend=None):
        if backend is None:
            import mido as backend
        self._backend = backend

    def list_outputs(self) -> list[str]:
        return list(self._backend.get_output_names())

    def send(self, port_name: str, data: bytes, gap_ms: int, _sleep=None) -> dict:
        import mido
        if port_name not in self.list_outputs():
            raise ValueError(f"unknown output port: {port_name}")
        messages = split_messages(data)   # raises ValueError on bad data
        if not messages:
            raise ValueError("no sysex messages in file")
        gap_s = max(0, min(2000, int(gap_ms))) / 1000.0
        sleep = _sleep if _sleep is not None else time.sleep
        with self._backend.open_output(port_name) as out:
            for i, raw in enumerate(messages):
                out.send(mido.Message.from_bytes(raw))
                if i < len(messages) - 1 and gap_s > 0:
                    sleep(gap_s)
        return {"messages": len(messages), "bytes": len(data)}


class SysexReceiver:
    """Always-on listener that saves incoming sysex dumps to the inbox.

    Put a synth in dump mode and the dump lands in `sysex_library/inbox/`
    (multi-part dumps coalesce via SysexLibrary.inbox_write's 2s window).
    Port names matching any of `patterns` (case-insensitive substring) are
    opened with a callback; a scan thread retries failures every retry_s
    and never lets an exception escape.
    """

    _MIN_BYTES = 8

    def __init__(self, library: SysexLibrary, patterns: list[str],
                 backend=None, retry_s: float = 5.0):
        if backend is None:
            import mido as backend
        self._backend = backend
        self._library = library
        self._patterns = [p.lower() for p in patterns if p]
        self._retry_s = retry_s
        self._ports: dict[str, object] = {}
        self._ports_lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def open_ports(self) -> list[str]:
        with self._ports_lock:
            return sorted(self._ports)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._scan_loop, daemon=True,
                                        name="sysex-receiver")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join()
        with self._ports_lock:
            for port in self._ports.values():
                try:
                    port.close()
                except Exception:  # noqa: BLE001 — best-effort close
                    pass
            self._ports.clear()

    def _matches(self, name: str) -> bool:
        low = name.lower()
        return any(p in low for p in self._patterns)

    def _on_message(self, msg) -> None:
        try:
            if msg.type != "sysex":
                return
            raw = bytes(msg.bytes())
            if len(raw) < self._MIN_BYTES:
                return
            self._library.inbox_write(raw)
        except Exception:  # noqa: BLE001 — callback must never raise into rtmidi
            pass

    def _scan_loop(self) -> None:
        while self._running:
            try:
                names = [n for n in self._backend.get_input_names() if self._matches(n)]
                with self._ports_lock:
                    for name in names:
                        if name in self._ports:
                            continue
                        try:
                            self._ports[name] = self._backend.open_input(
                                name, callback=self._on_message)
                        except Exception:  # noqa: BLE001 — retried next scan
                            pass
                    for name in list(self._ports):
                        if name not in names:
                            try:
                                self._ports.pop(name).close()
                            except Exception:  # noqa: BLE001
                                pass
            except Exception:  # noqa: BLE001 — scan must never kill the thread
                pass
            # Sleep in short intervals so stop() can return promptly
            remaining = self._retry_s
            while remaining > 0 and self._running:
                sleep_time = min(0.1, remaining)
                time.sleep(sleep_time)
                remaining -= sleep_time
