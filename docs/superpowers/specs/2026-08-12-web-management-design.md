# midicrt Web Management — Design

Date: 2026-08-12
Status: approved (brainstormed with operator; this document is the spec)

## Purpose

Extend midicrt v1's existing web service (`:8765`) with a management
surface usable from any LAN browser:

1. **Instrument editor** — edit the instrument name list live.
2. **Recordings manager** — browse/download/delete/rename/annotate the
   MIDI capture library; trigger a capture remotely.
3. **Sysex librarian** — upload/download/rename/delete `.syx` files,
   execute them out a chosen MIDI port, and automatically collect
   incoming sysex dumps from the rack into an inbox.

Context: the operator is pivoting from the midicrt2 strict rebuild
toward incrementally adopting v2 features into v1. This is the first
feature built under that policy. v2 code may be mined for ideas/portions
where useful.

## Architecture

One service, two surfaces. The existing observer core in
`web/observer.py` (SnapshotBridge, websocket dashboard) is not modified
beyond route registration. A new module `web/manage.py` registers:

- `GET /manage` — single-page management UI (static HTML/JS, same
  hand-rolled style as the existing dashboard; no external assets).
- `GET/POST /api/manage/...` — JSON endpoints (details per feature
  below). All state-changing operations are POST.

Posture: LAN-open, no auth, matching the rest of the rig. The
`read_only` contract blob served by `/healthz` is updated to state that
`/api/manage/*` is a write-capable management surface and the read-only
guarantee applies to the observer endpoints only.

### Ownership split

- **Through the app (IPC command socket `/tmp/midicrt.sock`)** — anything
  that is the running app's state:
  - `capture_recent` — same effect as the `C` key.
  - `set_instrument_names` — replaces `instruments.names`; the app
    updates its in-memory list and persists settings.json itself.
    Single-writer rule: the management service never writes
    settings.json directly, eliminating the write race with the app's
    own saves.
  - New commands are added to the engine `command_hooks` wiring in
    `midicrt.py`; the IPC protocol (envelopes, request_id) already
    exists in `engine/ipc.py::_handle_command`.
- **Direct (management service's own resources)**:
  - Sysex send/receive via the service's own mido/ALSA handles. ALSA is
    multi-client; this works even when the CRT app is down.
  - Recordings management as plain file I/O under `captures/`.

## Components

### `web/sysex_io.py` — sysex engine (no aiohttp imports)

- `SysexLibrary(root)` — file store rooted at `sysex_library/`
  (repo-relative sibling of `captures/`; contents gitignored, directory
  kept via `.gitkeep`). Subdir `inbox/` for received dumps.
  Operations: `list()` (name, size, message count, mtime), `read(name)`,
  `write(name, data)`, `rename(old, new)`, `delete(name)`,
  `promote(inbox_name, library_name)`. Names are sanitized to a safe
  charset; traversal outside the root is rejected.
- `split_messages(data: bytes) -> list[bytes]` — split a `.syx` blob on
  F0…F7 frames; reject files with garbage outside frames (report byte
  offset in the error).
- `SysexSender` — `send(port_name, data, gap_ms)` opens the mido output,
  sends each message, sleeps `gap_ms` between messages (default 20,
  clamped 0–2000). Returns per-message counts. Runs in a thread via
  `asyncio.to_thread` so the web loop never blocks on MIDI pacing.
- `SysexReceiver` — always-on listener thread started with the service.
  Opens mido inputs on ports matching a configurable pattern list
  (default: the USB-MIDI interface). Any received sysex message ≥ 8
  bytes is appended to the current inbox capture file
  (`inbox/YYYYMMDD-HHMMSS.syx`); messages arriving within 2s of each
  other coalesce into the same file (multi-part dumps), a fresh gap
  starts a new file. Port-open failures retry on a 5s cadence and never
  crash the service.

### `web/manage.py` — routes

Instruments:
- `GET /api/manage/instruments` → `{"names": [...]}` — read directly
  from settings.json (read-only, race-harmless). Length is whatever the
  list is — 16 today, more later with multiple devices. The UI renders
  one row per entry and never assumes 16.
- `POST /api/manage/instruments` `{"names": [...]}` → IPC
  `set_instrument_names`. The app validates: list of strings, each
  ≤ 32 chars, any length ≥ 1.

Recordings:
- `GET /api/manage/recordings` → entries for `captures/<stamp>/` dirs
  and `captures/pianoroll_exp/sessions/` files: name, kind, bytes,
  mtime, note (from `note.txt` sidecar if present), file list.
- `GET /api/manage/recordings/download?path=...` → file download
  (path validated inside `captures/`).
- `POST /api/manage/recordings/delete` / `rename` / `note` — file ops;
  rename sanitized; delete moves nothing to trash (these are the
  operator's own captures; homelab-grade).
- `POST /api/manage/capture` → IPC `capture_recent`, returns the app's
  reply (export path).

Sysex:
- `GET /api/manage/sysex` → library + inbox listings, current defaults
  (`default_output`, `send_gap_ms`), and live `mido` output port list.
- `POST /api/manage/sysex/upload` (multipart) — validated via
  `split_messages` before storing.
- `GET /api/manage/sysex/download?name=...`
- `POST /api/manage/sysex/rename` / `delete` / `promote`.
- `POST /api/manage/sysex/execute` `{"name", "port", "gap_ms"}` —
  sends via `SysexSender`; `port`+`gap_ms` become the remembered
  defaults (stored in a service-owned JSON beside the library,
  `sysex_library/manage-defaults.json` — NOT settings.json, to preserve
  the single-writer rule).

### UI (`web/static/manage.html` + `manage.js`)

One page, three panels (Instruments / Recordings / Sysex), fetch-based,
no frameworks, dark CRT-green aesthetic matching the existing dashboard.
Failure of any call surfaces as an inline red status line, never a
silent no-op.

## Error handling

- All handlers return `{"ok": false, "error": "..."}` with 4xx/5xx on
  failure; no stack traces to the client (logged server-side).
- IPC unavailable (app down): instruments/capture endpoints return 503
  with a clear "midicrt app is not running" error; sysex + recordings
  remain fully functional.
- Sysex execute refuses files that fail `split_messages` validation and
  port names not currently present.

## Testing

- Unit (unittest, existing repo style): `split_messages` (clean,
  multi-message, truncated, garbage-padded), `SysexLibrary` name
  sanitization + traversal rejection + inbox coalescing timestamps
  (injected clock), sender pacing plan (mocked port, assert message
  sequence + gaps).
- Route tests with aiohttp's test client for the JSON endpoints against
  a temp library/captures tree and a fake IPC client.
- Live verification on the Pi: full sysex round-trip (upload → execute
  to a loopback ALSA port → receiver saves to inbox → download and
  byte-compare), browser walk of all three panels, capture trigger
  observed in the app.

## Out of scope (this iteration)

- Auth of any kind.
- Editing arbitrary settings sections from the browser (Config page
  already does this on-device; a settings web editor can reuse this
  service later).
- Multi-device instrument schema changes — the editor deliberately
  treats the list length as data so the coming >16-channel /
  multi-device world only changes the app-side model, not this UI.
- Sysex request macros (sending dump-request commands to synths).
