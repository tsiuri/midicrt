"""Sysex file + MIDI mechanics for the web management surface.

No aiohttp imports here: this module is plain files + mido so it can be
unit-tested without a server and reused by anything else.
"""
from __future__ import annotations

import os
import re
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

    # -- name handling -------------------------------------------------
    def _resolve(self, name: str) -> str:
        rel = name[len("inbox/"):] if name.startswith("inbox/") else name
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
        now = time.time() if now is None else now
        if self._last_inbox_file is None or (now - self._last_inbox_ts) >= _INBOX_GAP_S:
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
            self._last_inbox_file = f"inbox/{stamp}.syx"
        self._last_inbox_ts = now
        with open(self._resolve(self._last_inbox_file), "ab") as f:
            f.write(data)
        return self._last_inbox_file
