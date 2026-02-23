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

## Layout

- midicrt.py: main program
- pages/: UI pages loaded at startup (IDs 0-9; see pages/help.py)
- plugins/: plugin handlers loaded at startup
- config/audiospectrum.json: config for the audio spectrum page
- config/chords.csv: chord interval database (imported)
- config/scales.csv: scale interval database (imported)
- instruments.txt: channel labels (16 entries)
- vars.txt: SDL framebuffer environment settings
- log.txt and midicrt_autoconnect.log: startup and autoconnect logs

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

## Plugins

Plugins in plugins/ are loaded automatically at startup in alphabetical
order. Each can implement handle(msg), draw(state), and other hooks.

| File               | Purpose                                         |
|--------------------|-------------------------------------------------|
| beat_counter.py    | Beat counter display                            |
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
- `CYCLE_PAGES = [1, 6, 8]` — page IDs to rotate through
- `INTERVAL = 15.0` — seconds between page switches
- `USER_PAUSE = 300` — seconds to pause cycling after any keypress

Cycling pauses for USER_PAUSE seconds whenever the user presses a key,
then resumes automatically.

### sysex.py — SysEx Command Receiver

Receives SysEx messages and dispatches them as commands. This allows remote
control from the Cirklon or any MIDI device that can send SysEx.

**Message format:**
```
F0  7D  6D  63  <cmd>  [args...]  F7
```
- `7D` — MIDI non-commercial / private-use manufacturer ID
- `6D 63` — ASCII `mc` (midicrt identifier)
- `<cmd>` — command byte
- `[args...]` — zero or more argument bytes (0–127)

**Commands:**

| Cmd  | Args     | Effect                                  |
|------|----------|-----------------------------------------|
| `01` | `<page>` | Switch to page 0–9                      |
| `02` | `00`     | Wake / deactivate screensaver           |
| `02` | `01`     | Force screensaver on immediately        |
| `03` | `00`     | Disable page cycler                     |
| `03` | `01`     | Enable page cycler                      |

Received commands are logged to the status line (row 2) so you can see
what arrived. Unknown commands are logged with their raw bytes.

**Example — switch to page 8 (Piano Roll):**
```
F0 7D 6D 63 01 08 F7
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

- 0-9: switch pages (0 is help)
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
