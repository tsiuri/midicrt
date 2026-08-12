# Web Management Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend midicrt v1's web service (:8765) with a management surface: live instrument-name editing, MIDI recordings management with remote capture trigger, and a full sysex librarian (upload/download/execute/receive-inbox).

**Architecture:** New `web/manage.py` registers `/manage` + `/api/manage/*` routes on the existing observer aiohttp app; `web/sysex_io.py` owns sysex file/MIDI mechanics with its own mido handles; app-state operations (capture, instruments) go through the existing IPC command socket — the management service never writes settings.json (single-writer rule).

**Tech Stack:** Python 3.13, aiohttp (already a dep of the observer), mido/python-rtmidi (already in the venv), unittest (repo style; NO pytest in the v1 venv — route tests use `aiohttp.test_utils.AioHTTPTestCase`).

## Global Constraints

- Work happens in `~/codex/midicrt` on pivisualizer (edit via motherbase scratchpad + scp, per session convention).
- The observer's existing read-only endpoints and `SnapshotBridge` are not modified except where a task says so.
- All state-changing HTTP endpoints are POST. All handlers return JSON `{"ok": true, ...}` or `{"ok": false, "error": "..."}` with 4xx/5xx.
- File paths from clients are always resolved and checked inside their root (`captures/` or `sysex_library/`); reject anything escaping.
- The IPC envelope protocol (engine/ipc.py): request `{"protocol_version": 1, "type": "command", "command": <str>, "payload": <dict>, "request_id": <str>}` + `\n`; response envelope has `type` of `"ack"` or `"error"` with matching `request_id`; interleaved `"snapshot"` envelopes must be skipped.
- Existing IPC commands already available (engine/core.py handle_command): `capture_recent` (payload `{"bars": int|None}`, ack contains `path`), `set_config` (payload `{"section": str, "value": dict}`).
- Instrument names: the app currently normalizes to exactly 16; the web layer must never assume a length (render whatever it reads; future multi-device rigs will grow this).
- Live service: SYSTEM unit `midicrt-web-observer.service`; restart with `sudo systemctl restart midicrt-web-observer` (askpass: `secret deploy_default_password` on motherbase).
- Commit after every task with the exact message given; never `git add -A` (settings.json churns at runtime).

---

### Task 1: IPC command client

**Files:**
- Create: `web/ipc_client.py`
- Test: `tests/test_web_ipc_client.py`

**Interfaces:**
- Produces: `send_command(command: str, payload: dict | None = None, socket_path: str = "/tmp/midicrt.sock", timeout_s: float = 3.0) -> tuple[bool, dict]` — `(True, ack_payload)` on ack, `(False, {"error": ...})` on error envelope, connect failure, or timeout. Blocking; callers run it via `asyncio.to_thread`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_web_ipc_client.py
import json
import os
import socket
import tempfile
import threading
import unittest

from web.ipc_client import send_command


class _FakeAppSocket:
    """Speaks the engine IPC protocol: replies to a command envelope after
    first emitting a snapshot line (clients must skip those)."""

    def __init__(self, response_builder):
        self._dir = tempfile.mkdtemp()
        self.path = os.path.join(self._dir, "sock")
        self._response_builder = response_builder
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.path)
        self._srv.listen(1)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        client, _ = self._srv.accept()
        with client, client.makefile("r", encoding="utf-8", newline="\n") as reader:
            line = reader.readline()
            req = json.loads(line)
            noise = {"protocol_version": 1, "type": "snapshot", "payload": {"x": 1}}
            client.sendall((json.dumps(noise) + "\n").encode())
            resp = self._response_builder(req)
            client.sendall((json.dumps(resp) + "\n").encode())

    def close(self):
        self._srv.close()


class SendCommandTest(unittest.TestCase):
    def test_ack_roundtrip_skips_snapshot_noise(self):
        def build(req):
            assert req["type"] == "command" and req["command"] == "capture_recent"
            return {"protocol_version": 1, "type": "ack",
                    "request_id": req["request_id"], "command": req["command"],
                    "payload": {"path": "/tmp/x.mid"}}

        fake = _FakeAppSocket(build)
        try:
            ok, data = send_command("capture_recent", {}, socket_path=fake.path)
        finally:
            fake.close()
        self.assertTrue(ok)
        self.assertEqual(data.get("path"), "/tmp/x.mid")

    def test_error_envelope_returns_false(self):
        def build(req):
            return {"protocol_version": 1, "type": "error",
                    "request_id": req["request_id"], "command": req["command"],
                    "payload": {"code": "invalid-args", "message": "nope"}}

        fake = _FakeAppSocket(build)
        try:
            ok, data = send_command("set_config", {"bad": 1}, socket_path=fake.path)
        finally:
            fake.close()
        self.assertFalse(ok)
        self.assertIn("nope", data.get("error", ""))

    def test_connect_failure_returns_false(self):
        ok, data = send_command("capture_recent", {}, socket_path="/tmp/does-not-exist-9x.sock",
                                timeout_s=0.5)
        self.assertFalse(ok)
        self.assertIn("error", data)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run (on the Pi): `cd ~/codex/midicrt && ~/codex/midicrt-venv/bin/python -m unittest tests.test_web_ipc_client -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.ipc_client'`

- [ ] **Step 3: Write minimal implementation**

```python
# web/ipc_client.py
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
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout_s)
            sock.connect(socket_path)
            sock.sendall((json.dumps(envelope) + "\n").encode("utf-8"))
            with sock.makefile("r", encoding="utf-8", newline="\n") as reader:
                while True:
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
    except (OSError, socket.timeout) as exc:
        return False, {"error": f"midicrt app unreachable: {exc}"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/codex/midicrt-venv/bin/python -m unittest tests.test_web_ipc_client -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add web/ipc_client.py tests/test_web_ipc_client.py
git commit -m "feat(web): blocking IPC command client for the manage surface"
```

---

### Task 2: sysex file mechanics — split_messages + SysexLibrary

**Files:**
- Create: `web/sysex_io.py`
- Create: `sysex_library/.gitkeep`, `sysex_library/inbox/.gitkeep`
- Modify: `.gitignore` (add `sysex_library/**` with `!**/.gitkeep` exceptions)
- Test: `tests/test_sysex_io.py`

**Interfaces:**
- Produces:
  - `split_messages(data: bytes) -> list[bytes]` — F0…F7 frames; raises `ValueError("byte outside sysex frame at offset N")` on garbage.
  - `class SysexLibrary(root: str)` with `list() -> list[dict]` (keys: `name, bytes, messages, mtime, inbox: bool`), `read(name) -> bytes`, `write(name, data)`, `rename(old, new)`, `delete(name)`, `promote(inbox_name, new_name)`, `inbox_write(data, now: float) -> str`. Names sanitized to `[A-Za-z0-9._ -]`, must end `.syx`; `ValueError` on bad names. Inbox files live under `root/inbox/`; inbox entries are addressed with the `inbox/` prefix in `name`.
  - Inbox coalescing: `inbox_write` appends to the file created by the previous call if `now - last_write_ts < 2.0`, else starts `inbox/YYYYMMDD-HHMMSS.syx` (from `time.strftime` on `now`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sysex_io.py
import os
import tempfile
import unittest

from web.sysex_io import SysexLibrary, split_messages


class SplitMessagesTest(unittest.TestCase):
    def test_single_message(self):
        raw = bytes([0xF0, 0x41, 0x10, 0xF7])
        self.assertEqual(split_messages(raw), [raw])

    def test_multi_message(self):
        m1 = bytes([0xF0, 0x41, 0xF7])
        m2 = bytes([0xF0, 0x42, 0x01, 0xF7])
        self.assertEqual(split_messages(m1 + m2), [m1, m2])

    def test_truncated_raises(self):
        with self.assertRaises(ValueError):
            split_messages(bytes([0xF0, 0x41, 0x10]))

    def test_garbage_between_frames_raises(self):
        raw = bytes([0xF0, 0x41, 0xF7, 0x00, 0xF0, 0x42, 0xF7])
        with self.assertRaises(ValueError) as ctx:
            split_messages(raw)
        self.assertIn("offset 3", str(ctx.exception))

    def test_empty_is_empty(self):
        self.assertEqual(split_messages(b""), [])


class SysexLibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = SysexLibrary(self.tmp.name)
        self.msg = bytes([0xF0, 0x41, 0x10, 0xF7])

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_list_read_roundtrip(self):
        self.lib.write("patch one.syx", self.msg)
        entries = self.lib.list()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "patch one.syx")
        self.assertEqual(entries[0]["messages"], 1)
        self.assertFalse(entries[0]["inbox"])
        self.assertEqual(self.lib.read("patch one.syx"), self.msg)

    def test_rename_delete(self):
        self.lib.write("a.syx", self.msg)
        self.lib.rename("a.syx", "b.syx")
        self.assertEqual([e["name"] for e in self.lib.list()], ["b.syx"])
        self.lib.delete("b.syx")
        self.assertEqual(self.lib.list(), [])

    def test_traversal_rejected(self):
        for bad in ("../evil.syx", "x/../../evil.syx", "/etc/passwd.syx", "noext"):
            with self.assertRaises(ValueError):
                self.lib.write(bad, self.msg)

    def test_inbox_coalescing_and_promote(self):
        f1 = self.lib.inbox_write(self.msg, now=1000.0)
        f2 = self.lib.inbox_write(self.msg, now=1001.0)   # <2s: same file
        f3 = self.lib.inbox_write(self.msg, now=1010.0)   # gap: new file
        self.assertEqual(f1, f2)
        self.assertNotEqual(f1, f3)
        inbox = [e for e in self.lib.list() if e["inbox"]]
        self.assertEqual(len(inbox), 2)
        two_msgs = [e for e in inbox if e["name"] == f1]
        self.assertEqual(two_msgs[0]["messages"], 2)
        self.lib.promote(f1, "kept.syx")
        names = [e["name"] for e in self.lib.list()]
        self.assertIn("kept.syx", names)
        self.assertNotIn(f1, names)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/codex/midicrt-venv/bin/python -m unittest tests.test_sysex_io -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.sysex_io'`

- [ ] **Step 3: Write minimal implementation**

```python
# web/sysex_io.py
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
                data = open(path, "rb").read()
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
        return open(self._resolve(name), "rb").read()

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/codex/midicrt-venv/bin/python -m unittest tests.test_sysex_io -v`
Expected: all tests PASS

- [ ] **Step 5: Add gitignore + placeholder dirs, commit**

Append to `.gitignore`:
```
sysex_library/**
!sysex_library/.gitkeep
!sysex_library/inbox/
!sysex_library/inbox/.gitkeep
```
Create empty `sysex_library/.gitkeep` and `sysex_library/inbox/.gitkeep`.

```bash
git add web/sysex_io.py tests/test_sysex_io.py .gitignore sysex_library/.gitkeep sysex_library/inbox/.gitkeep
git commit -m "feat(web): sysex library store + frame splitter"
```

---

### Task 3: SysexSender

**Files:**
- Modify: `web/sysex_io.py` (append class)
- Test: `tests/test_sysex_io.py` (append class)

**Interfaces:**
- Produces: `class SysexSender(backend=None)` (backend defaults to `mido`) with `list_outputs() -> list[str]` and `send(port_name: str, data: bytes, gap_ms: int) -> dict` returning `{"messages": N, "bytes": len(data)}`. Raises `ValueError` for unknown port or invalid data. Sleeps `gap_ms/1000` between messages (not after the last); `gap_ms` clamped 0–2000.

- [ ] **Step 1: Write the failing test (append to tests/test_sysex_io.py)**

```python
class _FakeMidoBackend:
    def __init__(self):
        self.sent = []
        self.sleeps = []

    def get_output_names(self):
        return ["Synth A 20:0", "Synth B 24:0"]

    def open_output(self, name):
        backend = self

        class _Out:
            def send(self, msg):
                backend.sent.append(bytes(msg.bytes()))

            def close(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.close()
                return False

        return _Out()


class SysexSenderTest(unittest.TestCase):
    def setUp(self):
        from web.sysex_io import SysexSender
        self.backend = _FakeMidoBackend()
        self.sender = SysexSender(backend=self.backend)
        self.two = bytes([0xF0, 0x41, 0xF7, 0xF0, 0x42, 0xF7])

    def test_sends_each_message_with_gaps(self):
        slept = []
        result = self.sender.send("Synth A 20:0", self.two, gap_ms=50, _sleep=slept.append)
        self.assertEqual(result, {"messages": 2, "bytes": 6})
        self.assertEqual(self.backend.sent,
                         [bytes([0xF0, 0x41, 0xF7]), bytes([0xF0, 0x42, 0xF7])])
        self.assertEqual(slept, [0.05])  # between messages only

    def test_unknown_port_rejected(self):
        with self.assertRaises(ValueError):
            self.sender.send("Nope 1:0", self.two, gap_ms=0)

    def test_invalid_data_rejected(self):
        with self.assertRaises(ValueError):
            self.sender.send("Synth A 20:0", b"\x00\x01", gap_ms=0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/codex/midicrt-venv/bin/python -m unittest tests.test_sysex_io.SysexSenderTest -v`
Expected: FAIL — `ImportError: cannot import name 'SysexSender'`

- [ ] **Step 3: Implement (append to web/sysex_io.py)**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/codex/midicrt-venv/bin/python -m unittest tests.test_sysex_io -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add web/sysex_io.py tests/test_sysex_io.py
git commit -m "feat(web): paced sysex sender with selectable output port"
```

---

### Task 4: SysexReceiver (always-on inbox listener)

**Files:**
- Modify: `web/sysex_io.py` (append class)
- Test: `tests/test_sysex_io.py` (append class)

**Interfaces:**
- Produces: `class SysexReceiver(library: SysexLibrary, patterns: list[str], backend=None, retry_s: float = 5.0)` with `start()`, `stop()`, `open_ports: list[str]`. Opens callback inputs on ports whose name contains any pattern substring (case-insensitive); on each `sysex` message with ≥ 8 raw bytes calls `library.inbox_write(bytes)`. Rescans/retries failed opens every `retry_s`. Never raises out of its thread.

- [ ] **Step 1: Write the failing test (append to tests/test_sysex_io.py)**

```python
class _FakeInBackend:
    def __init__(self, names):
        self.names = names
        self.callbacks = {}

    def get_input_names(self):
        return list(self.names)

    def open_input(self, name, callback):
        self.callbacks[name] = callback

        class _In:
            def close(self):
                pass

        return _In()


class SysexReceiverTest(unittest.TestCase):
    def test_saves_incoming_sysex_to_inbox(self):
        import mido
        from web.sysex_io import SysexReceiver
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        lib = SysexLibrary(tmp.name)
        backend = _FakeInBackend(["USB2.0-MIDI 24:0", "Midi Through 14:0"])
        rx = SysexReceiver(lib, patterns=["usb"], backend=backend, retry_s=0.05)
        rx.start()
        self.addCleanup(rx.stop)
        for _ in range(100):
            if rx.open_ports:
                break
            time.sleep(0.02)
        self.assertEqual(rx.open_ports, ["USB2.0-MIDI 24:0"])
        msg = mido.Message("sysex", data=[0x41, 0x10, 0x42, 0x12, 0x40, 0x00, 0x7F])
        backend.callbacks["USB2.0-MIDI 24:0"](msg)
        inbox = [e for e in lib.list() if e["inbox"]]
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0]["messages"], 1)

    def test_short_sysex_ignored(self):
        import mido
        from web.sysex_io import SysexReceiver
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        lib = SysexLibrary(tmp.name)
        backend = _FakeInBackend(["USB2.0-MIDI 24:0"])
        rx = SysexReceiver(lib, patterns=["usb"], backend=backend, retry_s=0.05)
        rx.start()
        self.addCleanup(rx.stop)
        for _ in range(100):
            if rx.open_ports:
                break
            time.sleep(0.02)
        backend.callbacks["USB2.0-MIDI 24:0"](mido.Message("sysex", data=[0x41]))
        self.assertEqual([e for e in lib.list() if e["inbox"]], [])
```

Also add `import time` to the test file imports if not present.

- [ ] **Step 2: Run test to verify it fails**

Run: `~/codex/midicrt-venv/bin/python -m unittest tests.test_sysex_io.SysexReceiverTest -v`
Expected: FAIL — `ImportError: cannot import name 'SysexReceiver'`

- [ ] **Step 3: Implement (append to web/sysex_io.py)**

```python
import threading


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
        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def open_ports(self) -> list[str]:
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
            self._thread.join(timeout=1.0)
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
            time.sleep(self._retry_s)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/codex/midicrt-venv/bin/python -m unittest tests.test_sysex_io -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add web/sysex_io.py tests/test_sysex_io.py
git commit -m "feat(web): always-on sysex inbox receiver"
```

---

### Task 5: manage routes — instruments + capture (and live instrument refresh in the app)

**Files:**
- Create: `web/manage.py`
- Modify: `midicrt.py` — `set_config_section` (line ~764)
- Test: `tests/test_manage_routes.py`

**Interfaces:**
- Consumes: `web.ipc_client.send_command` (Task 1 signature).
- Produces: `build_manage_app(deps: ManageDeps) -> list[aiohttp routes]` via `register_manage_routes(app, deps)`; `ManageDeps` dataclass with fields `settings_path: str`, `captures_root: str`, `library`, `sender`, `defaults_path: str`, `ipc: Callable` (defaults to `send_command`, injectable for tests). Routes this task: `GET /api/manage/instruments`, `POST /api/manage/instruments`, `POST /api/manage/capture`.

- [ ] **Step 1: Make instrument edits apply live in the app (modify midicrt.py)**

Replace:
```python
def set_config_section(section: str, value: dict):
    save_section(section, value)
```
with:
```python
def set_config_section(section: str, value: dict):
    save_section(section, value)
    if section == "instruments" and isinstance(value.get("names"), list):
        # Applied live: pages read INSTRUMENT_NAMES; mutate in place so
        # every existing reference sees the new names immediately.
        fresh = [str(n).strip() for n in value["names"] if str(n).strip()]
        if fresh:
            INSTRUMENT_NAMES[:] = fresh
```

- [ ] **Step 2: Write the failing route tests**

```python
# tests/test_manage_routes.py
import json
import os
import tempfile
import unittest

from aiohttp.test_utils import AioHTTPTestCase
from aiohttp import web as aioweb

from web.manage import ManageDeps, register_manage_routes
from web.sysex_io import SysexLibrary, SysexSender


class _FakeIPC:
    def __init__(self):
        self.calls = []
        self.reply = (True, {"message": "ok"})

    def __call__(self, command, payload=None, **kw):
        self.calls.append((command, payload))
        return self.reply


class _Base(AioHTTPTestCase):
    async def get_application(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.settings_path = os.path.join(root, "settings.json")
        json.dump({"instruments": {"names": ["A", "B"]}}, open(self.settings_path, "w"))
        self.captures = os.path.join(root, "captures")
        os.makedirs(self.captures)
        self.ipc = _FakeIPC()
        deps = ManageDeps(
            settings_path=self.settings_path,
            captures_root=self.captures,
            library=SysexLibrary(os.path.join(root, "sysex_library")),
            sender=SysexSender(backend=_NullBackend()),
            defaults_path=os.path.join(root, "manage-defaults.json"),
            ipc=self.ipc,
        )
        app = aioweb.Application()
        register_manage_routes(app, deps)
        return app

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()


class _NullBackend:
    def get_output_names(self):
        return []

    def open_output(self, name):
        raise ValueError("no ports")


class InstrumentsTest(_Base):
    async def test_get_names(self):
        resp = await self.client.get("/api/manage/instruments")
        body = await resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["names"], ["A", "B"])

    async def test_post_names_goes_via_ipc(self):
        resp = await self.client.post("/api/manage/instruments",
                                      json={"names": ["X", "Y", "Z"]})
        body = await resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(self.ipc.calls,
                         [("set_config", {"section": "instruments",
                                          "value": {"names": ["X", "Y", "Z"]}})])

    async def test_post_rejects_bad_payloads(self):
        for payload in ({"names": []}, {"names": "no"}, {"names": [1, 2]},
                        {"names": ["x" * 40]}, {}):
            resp = await self.client.post("/api/manage/instruments", json=payload)
            self.assertEqual(resp.status, 400)
        self.assertEqual(self.ipc.calls, [])


class CaptureTest(_Base):
    async def test_capture_forwards_to_ipc(self):
        self.ipc.reply = (True, {"message": "captured", "path": "/tmp/c.mid"})
        resp = await self.client.post("/api/manage/capture", json={})
        body = await resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["path"], "/tmp/c.mid")
        self.assertEqual(self.ipc.calls[0][0], "capture_recent")

    async def test_app_down_returns_503(self):
        self.ipc.reply = (False, {"error": "midicrt app unreachable: x"})
        resp = await self.client.post("/api/manage/capture", json={})
        self.assertEqual(resp.status, 503)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `~/codex/midicrt-venv/bin/python -m unittest tests.test_manage_routes -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.manage'`

- [ ] **Step 4: Implement web/manage.py (this task's slice)**

```python
# web/manage.py
"""Management surface routes: instruments, recordings, sysex librarian.

Registered by web/observer.py onto the same aiohttp app as the read-only
dashboard. Ownership split (see docs/superpowers/specs/2026-08-12-web-
management-design.md): app-state operations go through the IPC command
socket; sysex and recordings operate on this service's own resources.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from aiohttp import web

from web.ipc_client import send_command
from web.sysex_io import SysexLibrary, SysexSender, split_messages

_MAX_NAME_LEN = 32


@dataclass
class ManageDeps:
    settings_path: str
    captures_root: str
    library: SysexLibrary
    sender: SysexSender
    defaults_path: str
    ipc: Callable[..., tuple[bool, dict]] = field(default=send_command)


def _ok(**data: Any) -> web.Response:
    return web.json_response({"ok": True, **data})


def _fail(status: int, error: str) -> web.Response:
    return web.json_response({"ok": False, "error": error}, status=status)


def _ipc_response(ok: bool, data: dict) -> web.Response:
    if ok:
        return _ok(**data)
    message = data.get("error", "command failed")
    status = 503 if "unreachable" in message else 400
    return _fail(status, message)


def register_manage_routes(app: web.Application, deps: ManageDeps) -> None:
    async def get_instruments(request: web.Request) -> web.Response:
        try:
            with open(deps.settings_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            names = cfg.get("instruments", {}).get("names", [])
        except Exception:
            names = []
        return _ok(names=[str(n) for n in names])

    async def post_instruments(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _fail(400, "invalid JSON body")
        names = body.get("names")
        if (not isinstance(names, list) or not names
                or not all(isinstance(n, str) and 0 < len(n) <= _MAX_NAME_LEN for n in names)):
            return _fail(400, f"names must be a non-empty list of strings <= {_MAX_NAME_LEN} chars")
        ok, data = await asyncio.to_thread(
            deps.ipc, "set_config", {"section": "instruments", "value": {"names": names}})
        return _ipc_response(ok, data)

    async def post_capture(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        payload = {}
        if body.get("bars") is not None:
            payload["bars"] = body["bars"]
        ok, data = await asyncio.to_thread(deps.ipc, "capture_recent", payload)
        return _ipc_response(ok, data)

    app.router.add_get("/api/manage/instruments", get_instruments)
    app.router.add_post("/api/manage/instruments", post_instruments)
    app.router.add_post("/api/manage/capture", post_capture)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `~/codex/midicrt-venv/bin/python -m unittest tests.test_manage_routes -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add web/manage.py tests/test_manage_routes.py midicrt.py
git commit -m "feat(web): manage routes for instruments and capture; live instrument refresh"
```

---

### Task 6: manage routes — recordings

**Files:**
- Modify: `web/manage.py` (extend `register_manage_routes`)
- Test: `tests/test_manage_routes.py` (append class)

**Interfaces:**
- Consumes: `ManageDeps.captures_root`.
- Produces routes: `GET /api/manage/recordings`, `GET /api/manage/recordings/download?path=<rel>`, `POST /api/manage/recordings/delete {"path"}`, `POST /api/manage/recordings/rename {"path","new_name"}`, `POST /api/manage/recordings/note {"path","note"}`. `path` is captures-relative; entries are top-level capture dirs/files plus `pianoroll_exp/sessions/*` files.

- [ ] **Step 1: Write the failing tests (append to tests/test_manage_routes.py)**

```python
class RecordingsTest(_Base):
    def _seed(self):
        d = os.path.join(self.captures, "20260228-200249")
        os.makedirs(d)
        open(os.path.join(d, "capture.mid"), "wb").write(b"MThd" + b"\x00" * 20)
        s = os.path.join(self.captures, "pianoroll_exp", "sessions")
        os.makedirs(s)
        open(os.path.join(s, "engine-memory-abc.json"), "w").write("{}")

    async def test_list_download_note_rename_delete(self):
        self._seed()
        resp = await self.client.get("/api/manage/recordings")
        body = await resp.json()
        names = [e["path"] for e in body["entries"]]
        self.assertIn("20260228-200249", names)
        self.assertIn("pianoroll_exp/sessions/engine-memory-abc.json", names)

        resp = await self.client.get(
            "/api/manage/recordings/download",
            params={"path": "20260228-200249/capture.mid"})
        self.assertEqual(resp.status, 200)
        self.assertTrue((await resp.read()).startswith(b"MThd"))

        resp = await self.client.post("/api/manage/recordings/note",
                                      json={"path": "20260228-200249", "note": "good take"})
        self.assertTrue((await resp.json())["ok"])
        resp = await self.client.get("/api/manage/recordings")
        entry = [e for e in (await resp.json())["entries"]
                 if e["path"] == "20260228-200249"][0]
        self.assertEqual(entry["note"], "good take")

        resp = await self.client.post("/api/manage/recordings/rename",
                                      json={"path": "20260228-200249", "new_name": "good-take"})
        self.assertTrue((await resp.json())["ok"])

        resp = await self.client.post("/api/manage/recordings/delete",
                                      json={"path": "good-take"})
        self.assertTrue((await resp.json())["ok"])
        resp = await self.client.get("/api/manage/recordings")
        self.assertNotIn("good-take", [e["path"] for e in (await resp.json())["entries"]])

    async def test_traversal_rejected(self):
        resp = await self.client.get("/api/manage/recordings/download",
                                     params={"path": "../settings.json"})
        self.assertEqual(resp.status, 400)
        resp = await self.client.post("/api/manage/recordings/delete",
                                      json={"path": "../../etc"})
        self.assertEqual(resp.status, 400)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/codex/midicrt-venv/bin/python -m unittest tests.test_manage_routes.RecordingsTest -v`
Expected: FAIL — 404s (routes not registered)

- [ ] **Step 3: Implement (extend register_manage_routes in web/manage.py)**

Add inside `register_manage_routes`, plus one module-level helper:

```python
import re as _re
import shutil

_SAFE_SEGMENT = _re.compile(r"^[A-Za-z0-9._ -]+$")


def _resolve_capture_path(root: str, rel: str) -> str:
    """Resolve a captures-relative path, rejecting traversal."""
    parts = [p for p in rel.split("/") if p]
    if not parts or not all(_SAFE_SEGMENT.match(p) and p not in (".", "..") for p in parts):
        raise ValueError("invalid path")
    path = os.path.abspath(os.path.join(root, *parts))
    root_abs = os.path.abspath(root)
    if not (path == root_abs or path.startswith(root_abs + os.sep)):
        raise ValueError("path escapes captures root")
    return path
```

```python
    def _recording_entries() -> list[dict]:
        root = deps.captures_root
        entries: list[dict] = []

        def add(rel: str, path: str):
            note_path = (os.path.join(path, "note.txt") if os.path.isdir(path)
                         else path + ".note.txt")
            note = ""
            if os.path.isfile(note_path):
                note = open(note_path, "r", encoding="utf-8").read().strip()
            if os.path.isdir(path):
                files = sorted(os.listdir(path))
                size = sum(os.path.getsize(os.path.join(path, f))
                           for f in files if os.path.isfile(os.path.join(path, f)))
            else:
                files = []
                size = os.path.getsize(path)
            entries.append({"path": rel, "bytes": size, "mtime": os.path.getmtime(path),
                            "note": note, "files": files,
                            "kind": "dir" if os.path.isdir(path) else "file"})

        if os.path.isdir(root):
            for fn in sorted(os.listdir(root)):
                if fn == "pianoroll_exp" or fn.endswith(".note.txt"):
                    continue
                add(fn, os.path.join(root, fn))
        sessions = os.path.join(root, "pianoroll_exp", "sessions")
        if os.path.isdir(sessions):
            for fn in sorted(os.listdir(sessions)):
                if fn.endswith(".note.txt"):
                    continue
                add(f"pianoroll_exp/sessions/{fn}", os.path.join(sessions, fn))
        return entries

    async def get_recordings(request: web.Request) -> web.Response:
        return _ok(entries=_recording_entries())

    async def download_recording(request: web.Request) -> web.Response:
        rel = request.query.get("path", "")
        try:
            path = _resolve_capture_path(deps.captures_root, rel)
        except ValueError as exc:
            return _fail(400, str(exc))
        if not os.path.isfile(path):
            return _fail(404, "no such file")
        return web.FileResponse(path)

    async def delete_recording(request: web.Request) -> web.Response:
        body = await request.json()
        try:
            path = _resolve_capture_path(deps.captures_root, str(body.get("path", "")))
        except ValueError as exc:
            return _fail(400, str(exc))
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.isfile(path):
            os.remove(path)
        else:
            return _fail(404, "no such recording")
        return _ok()

    async def rename_recording(request: web.Request) -> web.Response:
        body = await request.json()
        new_name = str(body.get("new_name", ""))
        try:
            path = _resolve_capture_path(deps.captures_root, str(body.get("path", "")))
            if not _SAFE_SEGMENT.match(new_name):
                raise ValueError("invalid new_name")
            target = os.path.join(os.path.dirname(path), new_name)
        except ValueError as exc:
            return _fail(400, str(exc))
        if not os.path.exists(path):
            return _fail(404, "no such recording")
        if os.path.exists(target):
            return _fail(400, "target already exists")
        os.rename(path, target)
        return _ok()

    async def note_recording(request: web.Request) -> web.Response:
        body = await request.json()
        try:
            path = _resolve_capture_path(deps.captures_root, str(body.get("path", "")))
        except ValueError as exc:
            return _fail(400, str(exc))
        if not os.path.exists(path):
            return _fail(404, "no such recording")
        note_path = (os.path.join(path, "note.txt") if os.path.isdir(path)
                     else path + ".note.txt")
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(str(body.get("note", "")))
        return _ok()

    app.router.add_get("/api/manage/recordings", get_recordings)
    app.router.add_get("/api/manage/recordings/download", download_recording)
    app.router.add_post("/api/manage/recordings/delete", delete_recording)
    app.router.add_post("/api/manage/recordings/rename", rename_recording)
    app.router.add_post("/api/manage/recordings/note", note_recording)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/codex/midicrt-venv/bin/python -m unittest tests.test_manage_routes -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add web/manage.py tests/test_manage_routes.py
git commit -m "feat(web): recordings manager routes"
```

---

### Task 7: manage routes — sysex

**Files:**
- Modify: `web/manage.py` (extend `register_manage_routes`)
- Test: `tests/test_manage_routes.py` (append class)

**Interfaces:**
- Consumes: `SysexLibrary` (Task 2), `SysexSender` (Task 3), `ManageDeps.defaults_path`.
- Produces routes: `GET /api/manage/sysex` (library + inbox + ports + defaults), `POST /api/manage/sysex/upload` (multipart field `file`, optional `name`), `GET /api/manage/sysex/download?name=`, `POST .../rename {"name","new_name"}`, `POST .../delete {"name"}`, `POST .../promote {"name","new_name"}`, `POST .../execute {"name","port","gap_ms"}` (persists port/gap as defaults in `defaults_path` JSON).

- [ ] **Step 1: Write the failing tests (append to tests/test_manage_routes.py)**

```python
class _RecordingSendBackend(_NullBackend):
    def __init__(self):
        self.sent = []

    def get_output_names(self):
        return ["Synth A 20:0"]

    def open_output(self, name):
        backend = self

        class _Out:
            def send(self, msg):
                backend.sent.append(bytes(msg.bytes()))

            def close(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.close()
                return False

        return _Out()


class SysexRoutesTest(_Base):
    SYX = bytes([0xF0, 0x41, 0x10, 0xF7])

    async def get_application(self):
        app = await super().get_application()
        # swap in a recording backend for execute tests
        self.send_backend = _RecordingSendBackend()
        self._deps.sender = SysexSender(backend=self.send_backend)
        return app

    async def test_upload_list_download_roundtrip(self):
        from aiohttp import FormData
        form = FormData()
        form.add_field("file", self.SYX, filename="patch.syx",
                       content_type="application/octet-stream")
        resp = await self.client.post("/api/manage/sysex/upload", data=form)
        self.assertTrue((await resp.json())["ok"])

        resp = await self.client.get("/api/manage/sysex")
        body = await resp.json()
        self.assertEqual([e["name"] for e in body["library"]], ["patch.syx"])
        self.assertEqual(body["ports"], ["Synth A 20:0"])

        resp = await self.client.get("/api/manage/sysex/download",
                                     params={"name": "patch.syx"})
        self.assertEqual(await resp.read(), self.SYX)

    async def test_upload_rejects_invalid_sysex(self):
        from aiohttp import FormData
        form = FormData()
        form.add_field("file", b"\x00\x01", filename="bad.syx",
                       content_type="application/octet-stream")
        resp = await self.client.post("/api/manage/sysex/upload", data=form)
        self.assertEqual(resp.status, 400)

    async def test_execute_sends_and_remembers_defaults(self):
        self._deps.library.write("go.syx", self.SYX)
        resp = await self.client.post(
            "/api/manage/sysex/execute",
            json={"name": "go.syx", "port": "Synth A 20:0", "gap_ms": 0})
        body = await resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["messages"], 1)
        self.assertEqual(self.send_backend.sent, [self.SYX])
        defaults = json.load(open(self._deps.defaults_path))
        self.assertEqual(defaults["default_output"], "Synth A 20:0")

    async def test_execute_unknown_port_400(self):
        self._deps.library.write("go.syx", self.SYX)
        resp = await self.client.post(
            "/api/manage/sysex/execute",
            json={"name": "go.syx", "port": "Nope", "gap_ms": 0})
        self.assertEqual(resp.status, 400)
```

Note: `_Base.get_application` must stash `deps` on `self._deps` — add `self._deps = deps` just before `register_manage_routes(app, deps)` in the existing `_Base` class.

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/codex/midicrt-venv/bin/python -m unittest tests.test_manage_routes.SysexRoutesTest -v`
Expected: FAIL — 404s

- [ ] **Step 3: Implement (extend register_manage_routes in web/manage.py)**

```python
    def _load_defaults() -> dict:
        try:
            with open(deps.defaults_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"default_output": "", "send_gap_ms": 20,
                    "receive_patterns": ["usb"]}

    def _save_defaults(d: dict) -> None:
        with open(deps.defaults_path, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)

    async def get_sysex(request: web.Request) -> web.Response:
        entries = deps.library.list()
        return _ok(
            library=[e for e in entries if not e["inbox"]],
            inbox=[e for e in entries if e["inbox"]],
            ports=deps.sender.list_outputs(),
            defaults=_load_defaults(),
        )

    async def upload_sysex(request: web.Request) -> web.Response:
        reader = await request.multipart()
        data = b""
        name = ""
        async for part in reader:
            if part.name == "file":
                name = part.filename or "upload.syx"
                data = await part.read(decode=False)
            elif part.name == "name":
                name = (await part.text()).strip() or name
        if not data:
            return _fail(400, "no file uploaded")
        try:
            split_messages(data)
            if not name.endswith(".syx"):
                name += ".syx"
            deps.library.write(name, data)
        except ValueError as exc:
            return _fail(400, str(exc))
        return _ok(name=name)

    async def download_sysex(request: web.Request) -> web.Response:
        name = request.query.get("name", "")
        try:
            data = deps.library.read(name)
        except ValueError as exc:
            return _fail(400, str(exc))
        except FileNotFoundError:
            return _fail(404, "no such file")
        return web.Response(body=data, content_type="application/octet-stream",
                            headers={"Content-Disposition":
                                     f'attachment; filename="{os.path.basename(name)}"'})

    def _library_op(op):
        async def handler(request: web.Request) -> web.Response:
            body = await request.json()
            try:
                op(body)
            except ValueError as exc:
                return _fail(400, str(exc))
            except FileNotFoundError:
                return _fail(404, "no such file")
            return _ok()
        return handler

    rename_sysex = _library_op(
        lambda b: deps.library.rename(str(b.get("name", "")), str(b.get("new_name", ""))))
    delete_sysex = _library_op(lambda b: deps.library.delete(str(b.get("name", ""))))
    promote_sysex = _library_op(
        lambda b: deps.library.promote(str(b.get("name", "")), str(b.get("new_name", ""))))

    async def execute_sysex(request: web.Request) -> web.Response:
        body = await request.json()
        name = str(body.get("name", ""))
        port = str(body.get("port", ""))
        gap_ms = body.get("gap_ms", _load_defaults().get("send_gap_ms", 20))
        try:
            data = deps.library.read(name)
        except (ValueError, FileNotFoundError):
            return _fail(404, "no such file")
        try:
            result = await asyncio.to_thread(deps.sender.send, port, data, int(gap_ms))
        except ValueError as exc:
            return _fail(400, str(exc))
        defaults = _load_defaults()
        defaults["default_output"] = port
        defaults["send_gap_ms"] = int(gap_ms)
        _save_defaults(defaults)
        return _ok(**result)

    app.router.add_get("/api/manage/sysex", get_sysex)
    app.router.add_post("/api/manage/sysex/upload", upload_sysex)
    app.router.add_get("/api/manage/sysex/download", download_sysex)
    app.router.add_post("/api/manage/sysex/rename", rename_sysex)
    app.router.add_post("/api/manage/sysex/delete", delete_sysex)
    app.router.add_post("/api/manage/sysex/promote", promote_sysex)
    app.router.add_post("/api/manage/sysex/execute", execute_sysex)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/codex/midicrt-venv/bin/python -m unittest tests.test_manage_routes -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add web/manage.py tests/test_manage_routes.py
git commit -m "feat(web): sysex librarian routes (upload/download/execute/inbox)"
```

---

### Task 8: management UI page

**Files:**
- Create: `web/static/manage.html` (self-contained: inline CSS + JS, no external assets)
- Modify: `web/manage.py` — add `GET /manage` serving the file

**Interfaces:**
- Consumes every `/api/manage/*` route exactly as defined in Tasks 5–7.

- [ ] **Step 1: Add the route (web/manage.py, inside register_manage_routes)**

```python
    async def manage_page(request: web.Request) -> web.Response:
        page = os.path.join(os.path.dirname(__file__), "static", "manage.html")
        return web.FileResponse(page)

    app.router.add_get("/manage", manage_page)
```

- [ ] **Step 2: Write web/static/manage.html**

Single dark-green CRT-styled page (match `static/index.html`'s monospace/black-and-green look), three `<section>` panels, vanilla JS. Behavior requirements (implement exactly; ~200 lines):

- **Instruments**: on load `GET /api/manage/instruments`; render one text input per name (however many there are — never hardcode 16); "Save" POSTs `{names: [...]}` from the inputs (trimmed, empties dropped); status line shows success/error inline.
- **Recordings**: table from `GET /api/manage/recordings` (name, kind, size KB, date from mtime, note); per-row buttons: Download (opens `/api/manage/recordings/download?path=`, for dirs one link per file in `files`), Rename (prompt()), Note (prompt() prefilled), Delete (confirm()); top button "Capture now" → `POST /api/manage/capture` and shows returned `path`.
- **Sysex**: two tables (library, inbox) from `GET /api/manage/sysex`; upload via `<input type=file>` + POST FormData; per-row: Execute (uses the port `<select>` populated from `ports`, gap number input prefilled from `defaults.send_gap_ms`, select prefilled from `defaults.default_output`), Download, Rename, Delete; inbox rows get Promote (prompt for new name). Every action re-fetches its panel; every failure shows the returned `error` in a red status line.

- [ ] **Step 3: Verify page loads locally**

Run a syntax check and a serve test on the Pi after Task 9 deployment (the page is static; JS errors surface in the browser console during Task 9's live verification).

- [ ] **Step 4: Commit**

```bash
git add web/static/manage.html web/manage.py
git commit -m "feat(web): management UI page (instruments / recordings / sysex)"
```

---

### Task 9: wire into the observer service + live verification

**Files:**
- Modify: `web/observer.py` — route registration, receiver lifecycle, read_only contract text

**Interfaces:**
- Consumes: `register_manage_routes`, `ManageDeps`, `SysexLibrary`, `SysexSender`, `SysexReceiver`.

- [ ] **Step 1: Register in DashboardServer**

In `web/observer.py`, where the app/routes are built (`app.router.add_get("/", ...)` block), add:

```python
        from web.manage import ManageDeps, register_manage_routes
        from web.sysex_io import SysexLibrary, SysexReceiver, SysexSender

        repo_root = Path(__file__).resolve().parents[1]
        library = SysexLibrary(str(repo_root / "sysex_library"))
        deps = ManageDeps(
            settings_path=str(repo_root / "config" / "settings.json"),
            captures_root=str(repo_root / "captures"),
            library=library,
            sender=SysexSender(),
            defaults_path=str(repo_root / "sysex_library" / "manage-defaults.json"),
        )
        register_manage_routes(app, deps)
        try:
            import json as _json
            patterns = _json.load(open(deps.defaults_path)).get("receive_patterns", ["usb"])
        except Exception:
            patterns = ["usb"]
        self._sysex_receiver = SysexReceiver(library, patterns=patterns)
        self._sysex_receiver.start()
```

- [ ] **Step 2: Update the read_only contract**

In `_read_only_contract()`, change `"mode": "strict-read-only"` to
`"mode": "read-only-observer+management"` and add
`"management_surface": "/api/manage/* (POST mutations; LAN-trusted, no auth)"`.
Update the module docstring's read-only claim the same way.

- [ ] **Step 3: Run the full new test battery on the Pi**

Run: `~/codex/midicrt-venv/bin/python -m unittest tests.test_web_ipc_client tests.test_sysex_io tests.test_manage_routes -v`
Expected: all PASS

- [ ] **Step 4: Deploy + restart service**

```bash
# from motherbase
PW=$(~/scripts/secret deploy_default_password)
ssh pivisualizer "echo \"$PW\" | sudo -S systemctl restart midicrt-web-observer"
ssh pivisualizer 'sleep 3 && curl -s http://127.0.0.1:8765/healthz | head -c 300'
```
Expected: healthz ok; contract shows the new mode string.

- [ ] **Step 5: Live end-to-end verification**

```bash
# instruments: read, write one name, confirm on CRT notes page + read-back
curl -s http://pivisualizer.internal:8765/api/manage/instruments
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"names":["Kawai XD5","Matrix-1k","BassStaRack","Mnlogue","Arp 2600","Yamaha 1","Yamaha 2","Yamaha 3","Akai S 1","Akai S 2","Akai S 3","Akai S 4","Akai CD1","Akai CD2","Akai CD3","WEBTEST"]}' \
  http://pivisualizer.internal:8765/api/manage/instruments
# -> fb screenshot of page 1 must show WEBTEST on ch16; then restore real name.

# capture trigger
curl -s -X POST http://pivisualizer.internal:8765/api/manage/capture -d '{}'
# -> ok:true with a path under captures/

# sysex round-trip via ALSA loopback: create a virtual port pair with
# `sudo modprobe snd-virmidi` OR use amidi loopback; simpler: execute to the
# 'Midi Through' port while a temp receive pattern ["through"] is set in
# sysex_library/manage-defaults.json (restart service first):
curl -s -F "file=@/tmp/test.syx" http://pivisualizer.internal:8765/api/manage/sysex/upload
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"name":"test.syx","port":"Midi Through:Midi Through Port-0 14:0","gap_ms":20}' \
  http://pivisualizer.internal:8765/api/manage/sysex/execute
curl -s http://pivisualizer.internal:8765/api/manage/sysex   # inbox gained a file
# byte-compare the inbox download against /tmp/test.syx, then restore
# receive_patterns to ["usb"] and restart the service.

# recordings listing sanity
curl -s http://pivisualizer.internal:8765/api/manage/recordings | head -c 400
```
Also: open `http://pivisualizer.internal:8765/manage` in a browser (operator does this) and walk all three panels.

- [ ] **Step 6: Commit**

```bash
git add web/observer.py
git commit -m "feat(web): wire management surface + sysex receiver into the observer service"
```

---

## Self-review notes

- Spec coverage: instruments (T5+UI), recordings incl. trigger (T6, T5 capture, UI), sysex librarian incl. inbox receive (T2–T4, T7, UI), IPC ownership split (T1, T5), contract update (T9), gitignore (T2), variable-length instrument rule (T5 validation `any length ≥ 1`, UI never hardcodes 16). Receiver patterns configurable via `manage-defaults.json` (T7 defaults + T9 startup read).
- The app normalizes instrument names to 16 today (midicrt.py `load_instrument_names`); the web layer is length-agnostic per spec; app-side model growth is explicitly out of scope.
- Type consistency check: `ManageDeps` fields used identically in Tasks 5–9; `SysexLibrary` API in Tasks 2/4/7 matches; `send_command` signature in Tasks 1/5 matches.
