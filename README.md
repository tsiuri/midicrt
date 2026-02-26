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

### CRT compatibility constraints

- Character cells are the primary unit of layout.
- Rendering must remain meaningful in monochrome text terminals.
- Styling is limited to terminal-safe attributes (reverse/bold).
- Block graphics are optional enhancements, never required for core meaning.
- Pixel-oriented renderers must remain optional extras and never part of the
  default startup dependency chain.

### Incremental migration targets

Parity is being proved in stages:

1. Pixel parity milestone #1: Piano Roll widget + pixel presentation (implemented)
2. Transport (migrated)
3. Notes (next)
4. Event Log (next)


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
| `01` | `<page>` | Switch to any loaded page ID (currently 0–15) |
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

### zscreensaver.py — Screensaver

Blanks the screen after a period of MIDI silence to prevent burn-in.
Woken by any keypress or incoming note_on / note_off / CC message.

Configuration variables at the top of the file:

- `IDLE_TIMEOUT = 60.0` — seconds of MIDI silence before activating

## Keybindings

Global keys:

- 0-9: switch pages 0–9 (0 is help)
- t: switch to page 10 (Tuner)
- ! / @ / # / $ / %: switch to pages 11 / 12 / 13 / 14 / 15
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
