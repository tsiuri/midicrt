# midicrt

CRT-style MIDI monitor / visualizer for Cirklon.

## Autostart

On boot, the program starts on tty1 via:

- systemd getty autologin for user billie on tty1
  - /etc/systemd/system/getty@tty1.service.d/override.conf
- zsh login autostart in /home/billie/.zprofile
  - runs /home/billie/run_midicrt.sh for local tty1 sessions
- /home/billie/run_midicrt.sh activates the venv at
  /home/billie/codex/midicrt-venv and runs:
  python /home/billie/codex/midicrt/midicrt.py

Note: /etc/systemd/system/midicrt.service exists but is not enabled.

## Manual run

If you want to start it by hand:

- /home/billie/run_midicrt.sh

Or from the project directory after activating the venv:

- python /home/billie/codex/midicrt/midicrt.py

## What to look for after pulling (user-facing changes)

Use this section as a quick acceptance pass after `git pull`.

### 1) Startup/profile behavior (safety first)

Run:

```bash
./run_tui
```

Confirm:
- App starts in terminal mode with no pixel dependency requirements.
- `log.txt` receives a startup self-check line containing active profile/backend.

Optional pixel check:

```bash
MIDICRT_ENABLE_PIXEL=1 MIDICRT_PIXEL_RENDERER=sdl2 ./run_pixel
```

Confirm:
- Pixel profile starts when extras are installed.
- If extras are missing, startup falls back to text mode cleanly.

### 2) Deep-research data now visible in observer telemetry

Run observer in a second shell:

```bash
python scripts/run_web_observer.py --socket-path /tmp/midicrt.sock --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/` and confirm new user-visible panels populate:
- **Schema health** (snapshot version + compat mode).
- **Transport quality** (BPM, jitter, drift, status).
- **Microtiming summary** (bucket count + sample totals).
- **Capture status** (buffer fill, armed state, last commit path when present).

### 3) New structured widget surfaces (renderer parity)

From user POV, these should render consistently in text and pixel paths:
- Tempo quality panel.
- Microtiming histogram panel.
- Capture status panel.
- Module-health style status surfaces.

If one renderer shows stale/missing fields while another does not, treat it as a
parity regression.

### 4) Retrospective capture behavior

When triggering capture actions (`capture_recent` / `commit_last_bars`), confirm:
- Successful exports report metadata in snapshot payloads.
- `commit_last_bars` excludes the partial current bar and aligns to completed bars.
- Failure mode is explicit (`capture-failed`, e.g., no events in window).

### 5) Existing pages with recent feature growth

Check these user-visible behaviors while sending MIDI:
- **Notes page (1):** chord/scale confidence, missing tones, tension bar,
  harmonic rhythm, motif detector.
- **Piano roll (8):** smooth scrolling, out-of-range indicators, consistent
  behavior after leaving/returning to the page.
- **Audio spectrum (9):** recovers cleanly after device errors without spamming.
- **Time signature:** stable estimator output on transport + experimental page.

### 6) Quick regression checklist before declaring success

- No boot-time change to tty1 policy: `run_tui` remains default/autostart target.
- No required GUI dependency for default run path.
- tmux co-observe flow still works (`tmux attach -t midicrt`).
- Web observer remains read-only (monitoring only, no control API).

## Co-observing via tmux

A shared tmux session lets multiple people view the same terminal UI.
The session is launched with a persistent shell so it survives midicrt
being quit — after quitting you land at a zsh prompt and can relaunch.

- Create (if needed):
  - tmux new-session -d -s midicrt -n midicrt 'zsh -c "/home/billie/run_midicrt.sh; exec zsh"'
- Attach:
  - tmux attach -t midicrt
- Detach:
  - Ctrl-b d
- Relaunch after quit:
  - /home/billie/run_midicrt.sh  (from the shell prompt inside the session)

## Startup profiles

midicrt now supports three startup profiles:

- `run_tui` (default): terminal-safe Blessed/ANSI path used for tty1/autostart.
- `run_pixel` (optional): pixel path behind runtime feature flags and optional deps.
- `run_compositor` (optional): direct RGB565 framebuffer compositor path.

### Operational policy

- **tty1 autostart must always target `run_tui`**.
- Keep `run_tui` free of GUI/pixel imports to avoid headless boot failures.
- Use `run_pixel` only for explicitly provisioned environments.

### Commands

- TUI/default:
  - `./run_tui`
  - `python midicrt.py --profile run_tui`
- Pixel (optional):
  - `pip install '.[pixel]'`
  - `MIDICRT_ENABLE_PIXEL=1 MIDICRT_PIXEL_RENDERER=sdl2 ./run_pixel`
  - `python midicrt.py --profile run_pixel`
- Compositor (optional):
  - `python midicrt.py --profile run_compositor`

### Pixel renderer runtime controls

`run_pixel` reads defaults from `config/settings.json` under
`pixel_renderer`, then applies env var overrides at runtime:

- `MIDICRT_ENABLE_PIXEL=1` enables pixel backend selection for `run_pixel`.
- `MIDICRT_PIXEL_RENDERER` selects backend id (supported: `sdl2`, `pygame`,
  `kmsdrm`, `fb`, `framebuffer`).
- `MIDICRT_PIXEL_FULLSCREEN=1` enables fullscreen SDL mode (`0` disables).
- `MIDICRT_PIXEL_SCALE=2` multiplies cell metrics for window/display size.
- `MIDICRT_PIXEL_TARGET_FPS=30` sets pixel present-rate cap.
- `MIDICRT_PIXEL_CRT_TINT=30,255,100` overrides CRT tint RGB.

Fallback behavior:

- If `MIDICRT_ENABLE_PIXEL` is not set, `run_pixel` falls back to text.
- If pygame/SDL extras are unavailable, startup logs the reason and falls
  back to text.
- `run_tui` remains the tty-safe default and never imports pixel deps.

On startup, midicrt appends a self-check line to `log.txt` recording the active
profile and backend.


## Web observer (optional, read-only)

A lightweight web dashboard is available as a **separate process**. It reads the
same engine IPC snapshot stream used by local clients and exposes it over
WebSocket for remote viewing.

- Package path: `web/`
- Launcher script: `scripts/run_web_observer.py`
- Console entrypoint (after install): `midicrt-web-observer`
- Dashboard URL: `http://127.0.0.1:8765/` (default bind)
- WebSocket URL: `ws://127.0.0.1:8765/ws`

### Install

```bash
pip install '.[web]'
```

### Run

```bash
# from repo root
python scripts/run_web_observer.py --socket-path /tmp/midicrt.sock --host 127.0.0.1 --port 8765

# or if installed as a package
midicrt-web-observer --socket-path /tmp/midicrt.sock --host 127.0.0.1 --port 8765 --max-broadcast-hz 15
```

`--max-broadcast-hz` limits websocket fanout frequency and samples only the latest
snapshot, reducing CPU cost when many browsers are connected.

`--client-queue-size` configures per-client outbound buffering (default: `8`). When
clients are slower than the fanout cadence, the observer applies bounded
backpressure by dropping the oldest queued frame and keeping the newest one.
This keeps lag bounded for all clients and avoids unbounded memory growth.

### Security assumptions

- The observer remains **read-only** (no control/command API).
- Built-in auth and TLS termination are intentionally out of scope for the app
  process; keep the service bound to loopback (`127.0.0.1`) by default.
- For remote access, place the observer behind a hardened boundary such as SSH
  tunneling or a reverse proxy with auth + TLS.
- Keep tty1 autostart unchanged: `run_tui` remains the only boot-time target.
- tmux remains the primary operational interface; web observer is for passive
  monitoring only.

### Security deployment guidance (minimal)

Use one of the following patterns instead of embedding secrets in midicrt:

1. **SSH tunnel (quickest, no public listener)**

```bash
ssh -N -L 8765:127.0.0.1:8765 pi@your-host
# then browse locally: http://127.0.0.1:8765/
```

2. **Caddy reverse proxy (TLS + basic auth)**

```caddy
observer.example.com {
  reverse_proxy 127.0.0.1:8765
  basicauth {
    viewer JDJhJDE0JGR1bW15aGFzaC5yZXBsYWNlLndpdGgueW91ci5oYXNo
  }
}
```

3. **Nginx reverse proxy (TLS + HTTP auth)**

```nginx
server {
  listen 443 ssl;
  server_name observer.example.com;

  ssl_certificate /etc/letsencrypt/live/observer.example.com/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/observer.example.com/privkey.pem;

  auth_basic "midicrt observer";
  auth_basic_user_file /etc/nginx/.htpasswd;

  location / {
    proxy_pass http://127.0.0.1:8765;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
  }
}
```

Notes:
- Keep credentials and certificates in proxy-managed files or environment,
  never committed to this repository.
- If exposing beyond trusted LAN, add rate limits and IP allowlists at the
  proxy layer.

### Operator runbook: capacity and failure modes

Use `/healthz` as the first-line signal for observer health. It now includes
fanout telemetry counters:

- `telemetry.queue_dropped`: times a client queue hit capacity.
- `telemetry.queue_coalesced`: times the server removed stale queued payloads to
  prioritize the latest snapshot.

Capacity guidance:

- Start with `--max-broadcast-hz 10-20` and `--client-queue-size 8` for small
  groups (up to ~5 browser clients).
- For larger audiences or constrained CPUs, lower fanout first (for example,
  `--max-broadcast-hz 8`) before increasing queue size.
- Prefer small queue sizes (1-8) so stale frames are discarded quickly and
  active viewers see near-current state instead of delayed history.

Failure modes and operator response:

1. **Slow/paused browser tabs**
   - Symptom: `queue_dropped` and `queue_coalesced` rise while service remains
     healthy.
   - Response: expected under load; if persistent across many clients, reduce
     `--max-broadcast-hz` and verify host CPU headroom.
2. **Engine IPC disconnect/restart**
   - Symptom: `bridge.connected=false`, `bridge.reconnect_attempts` increases,
     `bridge.last_error` populated.
   - Response: observer auto-reconnects; if reconnects continue, validate engine
     socket path and engine process health.
3. **Snapshot freshness drift / stale deep research payloads**
   - Symptom: websocket payload `deep_research.stale=true` and increasing
     `deep_research.lag_ms`.
   - Response: treat as degraded analytics freshness; core transport data
     remains available while deep research catches up.

### Retrospective capture: commit last N bars

Operators can trigger retrospective capture using `capture_recent` (rolling window)
or `commit_last_bars` (bar-aligned window). `commit_last_bars` snaps the export
window to full bars ending at the most recently completed bar, so partial current
bar events are excluded by design.

Failure modes:
- If there are no buffered events in the selected window, capture returns
  `capture-failed` with a "no events" message.
- If tempo is currently unknown (`bpm=0`), export falls back to configured
  `default_bpm` for MIDI timing metadata.
- Snapshot payloads include `retrospective_capture.capture_metadata` so operators
  can verify `effective_tempo_map_segment`, `event_count`, `quantization_mode`,
  and `export_path` after each trigger.

## Layout

- midicrt.py: main program
- ui/: widget model + renderer backends (TTY-first architecture)
- pages/: UI pages loaded at startup (IDs 0-9; see pages/help.py)
- plugins/: plugin handlers loaded at startup
- config/settings.json: shared config for all tunables
- config/chords.csv: chord interval database (imported)
- config/scales.csv: scale interval database (imported)
- run_tui: default startup profile wrapper (tty-safe)
- run_pixel: optional pixel startup wrapper (feature-flagged)
- vars.txt: SDL/framebuffer environment settings for optional pixel runs
- log.txt and midicrt_autoconnect.log: startup/autoconnect logs

## CI test lanes

Pull requests use split test lanes so independent agent PRs do not queue behind unrelated suites:

- `track_a_tests`: scheduler, freshness, and contract-build tests.
- `track_b_tests`: fixture replay and deterministic logic tests.
- `integration_observer_tests`: web observer and snapshot-bridge integration tests.

Path filters gate each lane on pull requests so only relevant lanes run. A nightly scheduled workflow runs the full matrix (all three lanes) together.

### Runtime targets

- Per-lane target: **under 3 minutes**.
- Nightly full matrix target: **under 9 minutes** total wall-clock.

Each run publishes a `ci-lane-summary` artifact with lane status and runtime so bottlenecks are visible.

## Contribution workflow

- Parallel multi-agent workflow and handoff protocol:
  - `docs/parallel_dev_playbook.md`
- Deep-research ownership tracks and cross-track override policy:
  - `docs/contributor_tracks.md`

## Rendering architecture

The UI is now structured as a widget tree + renderer pipeline for incremental
page migration:

- `ui/model.py`: character-cell widget primitives (including `PianoRollWidget`).
- `ui/renderers/base.py`: renderer protocol.
- `ui/renderers/text/`: ANSI/Blessed implementation (`TextRenderer`).
- `ui/renderers/pixel.py`: optional pixel backend with dense piano-roll rendering.

Pages can implement either:

- `build_widget(state)` (new path, rendered by `TextRenderer`), or
- `draw(state)` (legacy path, still supported during migration).

### Event path migration (single engine route)

Legacy page/plugin event hooks now flow through a single engine path in `engine/core.py`:

1. MIDI message ingest and normalization (`MidiEngine.ingest`)
2. Transport update + module scheduling
3. Temporary compatibility shim routing (legacy page `handle`/`on_tick`, screensaver wake, poly display updates) gated by `core.module_scheduler.modules['legacy.event_shim'].enabled`
4. Snapshot publish with UI context (`ui_context`) and module outputs

`midicrt.py` now focuses on startup/profile/runtime wiring and no longer dispatches legacy page events directly.

### CRT compatibility constraints

- Character cells are the primary unit of layout.
- Rendering must remain meaningful in monochrome text terminals.
- Styling is limited to terminal-safe attributes (reverse/bold).
- Block graphics are optional enhancements, never required for core meaning.
- Pixel-oriented renderers must remain optional extras and never part of the
  default startup dependency chain.

### Incremental migration targets

Page/widget migration status (current):

1. ✅ Page widget migration: complete for all built-in pages (0–17 now expose `build_widget(state)`).
2. ✅ Pixel parity milestone #1: Piano Roll widget + pixel presentation (implemented).
3. ✅ Core parity pages completed: Transport, Notes, and Event Log are on the widget/render pipeline.
4. 🔄 Remaining migration effort is now integration hardening (engine/UI boundaries, tests, observer robustness), not new page ports.

### Current phase: integration hardening

Aligned with `deep-research-report.md`, the project has moved from architecture bring-up into integration hardening. Current focus areas are:

- finish remaining decoupling between engine internals and page-specific behavior,
- expand contract-level and tempo/IPC regression coverage,
- harden observer behavior for long-running multi-client use.

Before scaling to many simultaneous agents, use the go/no-go checklist: [`docs/parallel_readiness_checklist.md`](docs/parallel_readiness_checklist.md).
Track gate evidence and CI output in: [`docs/parallel_execution_board.md`](docs/parallel_execution_board.md).
Parallelizable migration slices are tracked on the execution board: [`docs/parallel_execution_board.md`](docs/parallel_execution_board.md).

### DeepResearch module contract (schema + versioning)

`engine/modules/interfaces.py` defines a `DeepResearchModule` protocol that consumes a strict snapshot subset matching `engine/state/schema.py` keys:

- required root keys: `schema_version`, `timestamp`, `transport`, `active_notes`, `module_outputs`, `diagnostics`, `ui_context`
- required transport keys: `tick`, `bar`, `running`, `bpm`
- output path: `modules.deepresearch` (optional `views.deepresearch`)
- snapshot transport contract for remote observers uses optional `deep_research` metadata fields: `produced_at` (unix seconds), `source_tick` (transport tick), `lag_ms` (freshness lag), `stale` (freshness boolean)

Cadence and budget policy are intentionally explicit in the interface and mirrored in `config/settings.json` under `deepresearch`:

- cadence policy: `every_tick` / `throttled_hz` / `event_triggered`
- cycle budget: `max_compute_ms`, `timeout_ms`, and `on_budget_exceeded` skip behavior
- failure semantics: retain last-good payload and publish status (`ok` / `skipped` / `error` / `disabled`) plus an error string when relevant

Versioning behavior:

- modules must record `schema_version_seen` in their output payload
- if `schema_version` changes incompatibly, the module should emit `status=error` (or `disabled`), keep last-good results marked `stale`, and avoid destructive output shape changes until the interface contract is updated
- new optional fields should be additive (TypedDict `NotRequired`) to preserve compatibility for older consumers

### How to land contract changes safely with parallel agents

Use this exact staged sequence whenever changing `ResearchContract` or DeepResearch payload shape:

1. **Classify the change**
   - Additive field only: bump minor contract version and keep major unchanged.
   - Breaking shape change: bump major contract version and plan staged rollout.
2. **Land reader compatibility first (all agents)**
   - Merge parsers/helpers that accept both current and next shape/version.
   - Keep writers emitting the old major version during this step.
3. **Add deterministic compatibility tests**
   - Current version success case.
   - Forward-compatible additive-field case (new minor read by old-minor expectation).
   - Explicit major mismatch failure with deterministic error payload.
4. **Roll out writer changes after reader saturation**
   - Switch builders to emit the new version only after all parallel agents are updated.
5. **Observe and then clean up**
   - Monitor for mismatch errors during mixed-version windows.
   - Remove old-shape compatibility in a follow-up change after stability window.


### Hardening checklist (remaining)

- [x] **Decoupling:** moved residual page-specific compatibility shims out of core scheduling paths and into explicit adapters.
- [~] **Tests:** broaden schema/IPC/tempo-map contract tests and add failure-mode coverage for renderer/runtime fallback.
- [~] **Observer hardening:** validate reconnect/backpressure behavior under sustained snapshot fan-out and document operational limits.

Status tags: `[ ]` not started, `[~]` in progress, `[x]` done.

### Integration roadmap (owner + estimate)

Future roadmap entries should include both owner and rough estimate in days for planning reliability.

1. `[x]` Engine/page decoupling pass — **Owner:** core/engine — **Estimate:** merged (0 days remaining)
2. `[~]` Contract + IPC + tempo-map test expansion — **Owner:** qa/infrastructure — **Estimate:** 2–3 days
3. `[ ]` Observer hardening and runbook updates — **Owner:** runtime/ops — **Estimate:** 1–2 days


## Pixel parity milestones

- **Milestone 1 (complete): Piano Roll parity in pixel backend**
  - Added a dedicated `PianoRollWidget` model carrying per-cell occupancy/intensity.
  - Pixel renderer now draws a dense piano-roll style with channel-aware shades/colors, while keeping text-like and monochrome-safe defaults.
  - Runtime style toggle available from config (`pianoroll.pixel_style`) and keybinding (`y`) on Page 8.

## Pages

| ID | Name            | Notes                        |
|----|-----------------|------------------------------|
| 0  | Help / Keys     | Key reference                |
| 1  | Notes           | Per-channel note display     |
| 2  | Send Notes      | Send notes from keyboard     |
| 3  | Transport       | BPM / bar / beat info        |
| 4  | CC Monitor      | Recent CC messages           |
| 5  | CC Dashboard    | CC graph (background)        |
| 6  | Event Log       | Filtered MIDI event log      |
| 7  | Program Changes | Program-change event log     |
| 8  | Piano Roll      | Multi-channel piano roll     |
| 9  | Audio Spectrum  | USB soundcard spectrum       |
| 10 | Tuner           | Audio pitch tuner            |
| 11 | Chord+Key       | Chord candidates + key       |
| 12 | Stuck Heatmap   | Stuck-note pitch heatmap     |
| 13 | Voice Monitor   | Per-channel polyphony        |
| 14 | Config          | Interactive settings editor  |
| 15 | TimeSig Exp     | Experimental meter estimate  |
| 16 | Piano Roll Exp  | Memory/live hybrid roll page |
| 17 | MIDI IMG2TXT    | MIDI + spectrum ASCII visuals |

## Plugins

Plugins in plugins/ are loaded automatically at startup in alphabetical
order. Each can implement handle(msg), draw(state), and other hooks.

| File               | Purpose                                         |
|--------------------|-------------------------------------------------|
| beat_counter.py    | Legacy placeholder (no-op; kept for load order) |
| beatflash.py       | Visual beat flash on bottom line                |
| loopprogress.py    | Loop progress bar                               |
| pagecycle.py       | Automatic page rotation (configurable)          |
| polydisplay.py     | Shared polyphonic note + CC state (not a plugin)|
| sysex.py           | SysEx command receiver (remote control)         |
| timeclock.py       | Musical + wall-clock + session timer            |
| zscreensaver.py    | Screensaver: blank screen after MIDI idle       |
| zstucknotes.py     | Stuck note monitor + warning overlay            |
| zharmony.py        | Chord/scale detector (recent notes)             |

### pagecycle.py — Page Cycler

Automatically rotates through a list of pages on a timer.

Configuration variables at the top of the file:

- `ENABLED = True` — set False to disable cycling entirely
- `CYCLE_PAGES = [1, 6, 8, 9]` — page IDs to rotate through
- `INTERVAL = 300` — seconds between page switches (default 5 minutes)
- `USER_PAUSE = 3600` — seconds to pause cycling after any keypress (default 60 minutes)

Cycling pauses for USER_PAUSE seconds whenever the user presses a key,
then resumes automatically.

### sysex.py — SysEx Command Receiver

Receives SysEx messages and dispatches them as commands. This allows remote
control from the Cirklon or any MIDI device that can send SysEx.

**Message formats:**
```
Legacy (still supported):
F0  7D  6D  63  <cmd>  [args...]  F7

Versioned (preferred):
F0  7D  6D  63  <ver>  <cmd>  [args...]  F7
```
- `7D` — MIDI non-commercial / private-use manufacturer ID
- `6D 63` — ASCII `mc` (midicrt identifier)
- `<ver>` — protocol byte (`41` = v1, `40` = negotiate highest supported)
- `<cmd>` — command byte
- `[args...]` — zero or more argument bytes (0–127)

**Version negotiation rule:**
- If `<ver>=40`, midicrt negotiates to its highest supported protocol version.
- If `<ver>=41`, protocol version is `<ver>-40` (so `41` = v1).
- Unsupported versions return an error reply containing supported versions.
- Frames without `<ver>` are treated as legacy/unversioned for backward compatibility.

**Commands:**

| Cmd  | Args     | Effect                                  |
|------|----------|-----------------------------------------|
| `01` | `<page>` | Switch to any loaded page ID (current default build includes 0–17) |
| `02` | `00`     | Wake / deactivate screensaver           |
| `02` | `01`     | Force screensaver on immediately        |
| `03` | `00`     | Disable page cycler                     |
| `03` | `01`     | Enable page cycler                      |
| `04` | `[bars]` | Capture recent bars (optional arg)      |
| `10` | *(none)* | Capabilities query (versioned only)     |

For versioned commands, midicrt sends a SysEx reply frame:
```
F0 7D 6D 63 <ver> <cmd> <status> [payload...] F7
```
where status is `00=ok`, `01=error`.

Received commands are logged in the footer/status area and to `sysex.log`
(+ split files in `sysex.d/`) so you can see what arrived. Unknown commands
are logged with their raw bytes.

Note: current startup prefers direct hardware MIDI input when available.
In that mode there may be no virtual `GreenCRT Monitor` input port exposed for
sequencer-loopback testing.

**Example — switch to page 8 (Piano Roll):**
```
F0 7D 6D 63 01 08 F7
```

**Example — versioned switch to page 8 (v1):**
```
F0 7D 6D 63 41 01 08 F7
```

**Example — capabilities query with negotiation:**
```
F0 7D 6D 63 40 10 F7
```

**Testing from the command line** (using the venv):
```
source ~/codex/midicrt-venv/bin/activate
python3 -c "
import mido, time
mido.set_backend('mido.backends.rtmidi')
with mido.open_output('GreenCRT Monitor') as p:
    p.send(mido.Message('sysex', data=(0x7D, 0x6D, 0x63, 0x01, 0x08)))
    time.sleep(0.1)
"
```

### MIDI send helper CLI (preferred)

Use the repo helper for interactive/testing sends instead of hand-writing
`aseqsend` byte strings each time:

```bash
./midisend list
./midisend note C4 --ch 1 --vel 96 --dur-ms 140
./midisend cc 74 100 --ch 2
./midisend pc 12 --ch 1
./midisend sysex 7D 6D 63 41 01 08
```

Notes:
- `./midisend` resolves to `scripts/midisend.py`.
- Default destination port is auto-detected (override with `--port` or `MIDISEND_PORT`).
- `sysex` auto-wraps with `F0`/`F7` when omitted.

### zscreensaver.py — Screensaver

Blanks the screen after a period of MIDI silence to prevent burn-in.
Woken by any keypress or incoming note_on / note_off / CC message.

Configuration variables at the top of the file:

- `IDLE_TIMEOUT = 60.0` — seconds of MIDI silence before activating

## Keybindings

Global keys:

- 0-9: switch pages 0–9 (0 is help)
- t: switch to page 10 (Tuner)
- ! / @ / # / $ / % / ^ / &: switch to pages 11 / 12 / 13 / 14 / 15 / 16 / 17
- q or Esc: quit

Page-specific keys:

- Page 2 (Send Notes):
  - Note keys: z s x d c v b n j m l ; /
  - , / .: channel down/up
  - [ / ]: octave down/up
  - - / =: velocity down/up
  - g / h: gate time down/up
- Page 6 (Event Log):
  - f: enter CC filter input mode (type digits)
  - *: clear all filters
  - Filter mode: Enter apply, Esc cancel, Backspace delete
  - Scroll: Up/Down, PgUp/PgDn, Home/End
- Page 8 (Piano Roll):
  - v: edit visible channels (list like 1,2,5-8)
  - d: toggle channel 10
  - *: show all channels
  - PgUp/PgDn: shift pitch range by octave
  - Home: reset pitch range
  - y: toggle piano-roll pixel style (`text`/`dense`)
- Page 9 (Audio Spectrum):
  - [ ] or { }: bins down/up
  - g / h: gain +/-
  - s / a: smoothing +/-
  - f / v: dB floor up/down
  - c / x: dB ceiling up/down
  - j / k: display scale down/up
  - z: toggle auto-adapt
  - Z: reset display scale
  - l: toggle freq scale (log/lin)
  - m: toggle agg mode (avg/max)
  - n / N: low-cut down/up
  - p: toggle HPF
  - , / .: device prev/next
  - 0: default device
  - r: refresh devices
- Page 17 (MIDI IMG2TXT):
  - `[`: lower block size (higher detail)
  - `]`: raise block size (chunkier img2txt)
  - c: cycle character ramps
  - i: invert brightness mapping
  - g / h: gamma down/up
  - a: toggle audio-reactive processing on/off (MIDI-only mode when off)
  - j / k: lower/raise Page 17 FPS cap
  - u: toggle auto quality scaling
