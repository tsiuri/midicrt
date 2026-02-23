# AGENTS.md - midicrt

This file tracks launch, config, and development notes for midicrt.

## Entrypoint

- /home/billie/run_midicrt.sh
  - activates /home/billie/codex/midicrt-venv
  - runs /home/billie/codex/midicrt/midicrt.py

## Git

- https://github.com/tsiuri/midicrt
- Remote: origin/master

## Autostart

- /etc/systemd/system/getty@tty1.service.d/override.conf (autologin billie on tty1)
- /home/billie/.zprofile (runs run_midicrt.sh for tty1 local sessions)
- /etc/systemd/system/midicrt.service exists but is not enabled

## Co-observing via tmux

- Session name: midicrt
- Create (if needed): tmux new-session -d -s midicrt -n midicrt 'zsh -c "/home/billie/run_midicrt.sh; exec zsh"'
- Attach: tmux attach -t midicrt
- Detach: Ctrl-b d

The session uses 'exec zsh' so it persists as a shell after midicrt
quits. Relaunch from inside the session with /home/billie/run_midicrt.sh

## Config and logs

- /home/billie/codex/midicrt/config/settings.json
- /home/billie/codex/midicrt/log.txt
- /home/billie/codex/midicrt/midicrt_autoconnect.log
- /home/billie/codex/midicrt/instruments.txt (legacy; migrated into settings.json)
- /home/billie/codex/midicrt/vars.txt

## Configuration policy (IMPORTANT)

Use a shared JSON config file for all tunables going forward:
- /home/billie/codex/midicrt/config/settings.json
- Each module/page/plugin gets its own top-level section (e.g., "audiospectrum").
- New configurable constants should be migrated into this shared file.
- Future changes must read/write settings via this shared config; do not create new per-module config files.

## Structure

- pages/: UI pages (optional keypress handlers)
- plugins/: plugin modules
- config/: local config files

## MIDI backend

- midicrt.py sets the mido backend to rtmidi and uses aconnect for autoconnect

## Plugin hooks

Plugins can implement:
- handle(msg): called for every note_on, note_off, control_change, program_change
- draw(state): called every UI frame; state has keys: tick, bar, running, bpm, cols, rows, y_offset
- notify_keypress(): called by keyboard_listener for any keypress not swallowed by screensaver
- is_active() / deactivate(): used by keyboard_listener to detect and dismiss the screensaver

Plugin load order is alphabetical by filename. Draw order follows the same
sequence, so plugins named with 'z' prefix draw last (on top).

## Key plugin / midicrt.py interactions

### Screensaver (plugins/zscreensaver.py)
- keyboard_listener finds it via duck-typing (is_active + deactivate) and calls
  deactivate() on any keypress
- handle_midi() finds it the same way and calls deactivate() on note/CC activity
- Configured via IDLE_TIMEOUT at top of file (default 60 s)

### SysEx receiver (plugins/sysex.py)
- handle_midi() has a dedicated `elif msg.type == "sysex"` branch that forwards
  to all plugins (sysex is NOT forwarded via the note/CC branch)
- sysex.py checks for prefix (0x7D, 0x6D, 0x63) and dispatches by command byte
- Commands are logged to AUTOCONNECT_LOG (visible on row 2)
- To add new commands: add an elif block in sysex._dispatch()

### Page cycler (plugins/pagecycle.py)
- keyboard_listener finds it via duck-typing (notify_keypress) and calls
  notify_keypress() on any keypress to pause auto-cycling
- Configured via ENABLED, CYCLE_PAGES, INTERVAL, USER_PAUSE at top of file

### Header redraw (midicrt.py ui_loop)
- last_header is reset to "" on page switch and on terminal resize to force
  the tab bar (row 0) to redraw after a screen clear

## After making changes

After any code change, restart the running midicrt instance and verify the fix:

1. Kill the process (q may be swallowed by screensaver; use kill if needed):
   - tmux send-keys -t midicrt:0 q ''
   - or: kill $(ps aux | grep "midicrt.py" | grep -v grep | awk '{print $2}')
2. Wait a moment, then relaunch (session persists as a shell after quit):
   - tmux send-keys -t midicrt:0 '/home/billie/run_midicrt.sh' Enter
3. Capture the pane to confirm it started and looks correct:
   - tmux capture-pane -t midicrt:0 -p

## Recent Work Summary (2026-02)

Key changes and features added during recent work. Use this as the handoff
reference for future changes.

**Config System**
- All tunables moved into shared JSON: `codex/midicrt/config/settings.json`.
- New helper: `codex/midicrt/configutil.py` for load/save sections.
- Legacy files removed: `codex/midicrt/config/audiospectrum.json`, `codex/midicrt/instruments.txt`.
- Config editor page added: `pages/configui.py` (Page 14, key `$` / Shift+4).
  - Supports navigating dict/list, editing values, +/- adjust with acceleration.
  - Edit mode exits with Esc/Enter/Backspace/Ctrl-C/Ctrl-G.
  - Acceleration parameters are configurable under `configui`.
- Config migration coverage:
  - `audiospectrum`, `instruments`, `voice_monitor`, `stuck_notes`, `harmony`,
    `tuner`, `screensaver`, `pagecycle`, `pianoroll`, `eventlog`,
    `core` (FPS/header scroll), `panic` (panic output), `timesig`, `timesig_exp`.

**New Pages and Keys**
- Page 10 (`t`): Tuner.
- Page 11 (`!` / Shift+1): Chord+Key (top 3 chord candidates + key estimate).
- Page 12 (`@` / Shift+2): Stuck Heatmap.
- Page 13 (`#` / Shift+3): Voice Monitor.
- Page 14 (`$` / Shift+4): Config editor.
- Page 15 (`%` / Shift+5): Experimental time signature page.

**Audio / Tuner**
- `pages/tuner.py` uses aubio pitch; shares audio input with `pages/audiospectrum.py`.
- `pages/audiospectrum.py` now exposes raw audio tap and device helpers.
- aubio built for Python 3.13 (local patch for Numpy 2.x const signature).

**Harmony / Chords / Scales**
- CSV databases created from plucknplay (chords/scales).
- `harmony.py` supports tied matches: if <=3 ties, all shown; if >3 ties, no match.
- Notes page shows 4 slots each for chord/scale history plus stats.
- Chord display reverse-text while notes held; scale backlighting drops if last note outside scale.
- Per-scale stats tracked: inside/total and unique inside/unique total.

**Stuck Notes**
- `plugins/zstucknotes.py` monitors stuck notes, logs to `log.txt`, and sends panic
  (All Notes Off) via USB MIDI output when critical threshold hits.
- Configurable thresholds and behavior in `settings.json` under `stuck_notes`.
- Stuck Heatmap page shows pitch-class counts and top stuck notes.

**Voice Monitor**
- `plugins/zvoicemonitor.py` monitors polyphony; per-channel limits added.
- UI shows instrument names with active/limit per channel.
- Config in `settings.json` under `voice_monitor`, including `per_channel_limits`.

**Event Log**
- Note-off/velocity-0 entries now include precise duration in brackets.
- Configurable max rows in `settings.json` under `eventlog`.

**Piano Roll**
- Out-of-range indicators (top/bottom) with reverse text; configurable hold.
- Up/Down keys pan range; swapped so direction matches screen movement.

**SysEx Logging**
- `plugins/sysex.py` logs all SysEx to `sysex.log` and per-message files in `sysex.d/`.

**Time Signature**
- Primary estimator: `plugins/ztimesig.py` shows on Transport page.
  - Rolling window + decay, change detection, collapses same-tick chords.
  - Retains last value on stop; resets on next start.
  - Displays events window/total and pending change.
- Experimental estimator: `plugins/ztimesig_exp.py` with separate page (15).
  - Uses beat/downbeat scoring with priors; also collapses same-tick chords.
  - Config in `settings.json` under `timesig_exp`.

**Header / Status**
- AutoConnect message now right-aligned on transport row with a scrolling window.
  Row 2 is blank again for extra space.

**Zsh / tmux**
- `.zshrc` function `midicrt()` starts or attaches to tmux session.
- Avoids nested tmux via `tmux switch-client` when already inside.

## Session Notes (2026-02-19)

Changes and fixes made during this session. All changes below are on top of
the baseline documented in "Recent Work Summary" above.

### audiospectrum.py — bug fixes and CPU optimization

**Bugs fixed:**
- PortAudio/ALSA errors were spamming every draw frame because `_ensure_thread()`
  restarted the audio thread with no cooldown. Fixed with `_THREAD_COOLDOWN = 5`
  seconds — thread restarts are rate-limited.
- `_error_msg` was never cleared after the stream recovered, so the error banner
  persisted even when audio was working. Fixed: set `_error_msg = None` inside the
  `with sd.InputStream(...)` block on successful open.
- SyntaxError: duplicate `global _error_msg` declarations (one in `with` block,
  one in `except` block). Fixed by hoisting all globals to top of `_audio_loop()`:
  `global _ready, _sr, _last_levels, _error_msg`.

**CPU optimization (98% → ~34% on spectrum page, ~29% idle):**
- Added `_rebuild_cache()`: precomputes Hanning window, `win_power`, and band
  start/end indices via `np.searchsorted`. Cache is invalidated only when sr,
  blocksize, bins, freq_scale, or FMIN_HZ change.
- Replaced Python HPF for-loop with vectorized numpy geometric prefix sum.
- Batched all `_draw_bars` terminal writes into a single `sys.stdout.write` call.
- Added page-check in the audio callback: skips FFT computation when the current
  page is not in `_ACTIVE_PAGE_IDS`. This eliminates FFT overhead while on other
  pages (piano roll, notes, etc.).

### pagecycle.py — added audio spectrum to rotation

Changed `CYCLE_PAGES = [1, 6, 8]` → `[1, 6, 8, 9]` to include Page 9
(Audio Spectrum) in the auto-cycling rotation.

### loopprogress.py — removed debug label

Removed `"LOOPTEST"` debug string that had been left in the loop progress display.
Replaced with spaces of the appropriate width.

### pianoroll.py — background scroll thread

**Problem:** Notes held while navigating away from the piano roll page appeared as
very long notes when returning, because `_shift_if_needed()` only ran on draw frames
(i.e. only when the page was active).

**Fix:** Added a background daemon thread (`pianoroll-bg`) that calls
`_shift_if_needed()` at ~20 Hz using `midicrt.tick_counter`, `midicrt.running`,
and `midicrt.bpm` globals. The thread is started lazily by `_ensure_bg()` which
`draw()` calls on each frame. The piano roll buffer now scrolls continuously
regardless of which page is active.

Key additions:
```python
def _bg_loop():
    import midicrt as mc
    while True:
        _shift_if_needed({"tick": mc.tick_counter, "running": mc.running, "bpm": mc.bpm})
        time.sleep(0.05)

def _ensure_bg():
    global _bg_thread
    if _bg_thread and _bg_thread.is_alive():
        return
    _bg_thread = threading.Thread(target=_bg_loop, daemon=True, name="pianoroll-bg")
    _bg_thread.start()
```

### zharmony.py — tension score, harmonic rhythm, motif detection

**`get_tension(active_pcs)`** — returns `(score 0.0–10.0, label, worst_ic_name)`
for a set of currently active pitch classes. Uses an interval-class dissonance
table (psychoacoustic roughness model). Labels: silent / consonant / mild / tense /
dissonant / harsh. Names the worst interval when it's m2/M7, M2/m7, or tritone.

```python
_IC_DISSONANCE = [0.0, 1.0, 0.8, 0.3, 0.1, 0.2, 1.0]  # ic 0–6
```

**`get_harmonic_rhythm(bpm)`** — returns `(changes_per_bar, label)`. Tracks
timestamps of chord label changes in `_chord_change_times` (deque, maxlen=16).
Averages the last 4 inter-change intervals and converts to chord changes per bar
assuming 4/4. Labels: static / slow / moderate / fast / very fast. Returns
`(None, '')` until at least 2 chord changes have been recorded.

**`get_motif_info(window=3)`** — returns `(found, pattern_str, count)`. Tracks
signed semitone intervals between consecutive note-ons in `_interval_history`
(deque, maxlen=64, newest first via `appendleft`). After each note, checks whether
the last `window` intervals appear again anywhere in the buffer. Because intervals
(not absolute pitches) are compared, transpositions match automatically — C-E-D and
F-A-G both encode as `+4 -2`. Returns `(False, '', 0)` if not enough history or
no repetition found.

State added to module level:
```python
_chord_change_times = deque(maxlen=16)
_last_note_for_iv = None
_interval_history = deque(maxlen=64)
```

### pages/notes.py (Page 1) — new display rows

All rows are conditional on terminal height; they are silently skipped if there
is not enough space.

**Chord/scale confidence + missing tones** (`info_y + 6`, `info_y + 7`):
- `Chord conf: 0.87  missing: F` — ratio of matched chord tones and any missing
  tones from the chord template.
- `Scale conf: 0.91  missing: B♭` — same for the current scale.
- Uses `zharmony.get_chord_info()` / `get_scale_info()` and `harmony.NOTE_NAMES`.

**Tension bar** (`info_y + 9`):
- `Tension: ████████░░░░░░░░░░░░  4.2  tense  [tritone]`
- Block bar (20 chars), numeric score, label, and worst interval name.
- **Hold/decay**: if fewer than 2 pitch classes are currently sounding (staccato),
  the last non-trivial result is held for `_TENSION_HOLD_SECS = 1.5` seconds before
  decaying to 0.0/silent. This prevents the score from reading 0.0 between notes.

**Harmonic rhythm** (`info_y + 10`):
- `Harm.rhy: 2.0 ch/bar  fast`
- Reads `midicrt.bpm` for the conversion from seconds to bars.

**Motif detector** (`info_y + 11`):
- `Motif:  +4 -2  [x3]` — interval pattern and how many times it has recurred.
- Shows `--` when no repetition detected yet.

## Testing SysEx (page switch)

midicrt listens on the ALSA sequencer input (`RtMidiIn Client`), not the raw
hardware port. Use `aseqsend` to send SysEx to that sequencer port.

1. Find the target port:
   - aseqsend -l
   - Expect a line like: `128:0    RtMidiIn Client  GreenCRT Monitor`
2. Send a page-switch SysEx (example: page 5 = CC Dashboard):
   - aseqsend -p 128:0 "F0 7D 6D 63 01 05 F7"
3. Confirm in tmux:
   - tmux capture-pane -t midicrt:0 -p

If you instead use `amidi -p hw:...`, the message goes to the raw hardware
device and will NOT reach midicrt’s sequencer input.
