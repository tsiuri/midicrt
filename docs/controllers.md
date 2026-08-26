# midicrt synth controllers — guide & runbook

The "controllers-in-midicrt" program: hardware synth/effects editors that run
as CRT pages on pivisualizer, as a phone/tablet web surface, and as an Android
app — all driven from one set of device definitions, with the M-VAVE SMK-25
mini as the physical knob/keyboard. Built 2026-08-24/25.

## Architecture (one definition, three surfaces)

```
devices/<dev>.py      pure data + MIDI builders (+ decoders) — the ONLY place
                      protocol knowledge lives. No midicrt/mido imports.
     ├── pages/<dev>.py         CRT page (blessed/compositor text UI)
     ├── web/control.py         DeviceSession per device -> schema-driven
     │     └── web/static/control.html   touch UI (sliders/selects, live SVG ADSR)
     └── (Android)  mothership:~/projects/synth-controllers-android  WebView shell
plugins/knobctl.py    SMK-25 router: knob -> focused field, notes -> page channel
plugins/devicesync.py hardware -> GUI mirroring (units that transmit)
```

Adding a controller = a `devices/` module + a page + ~40 lines of web session.
The web/Android surfaces pick it up automatically (schema-driven).

### Page contract (what every controller page implements)

| symbol | purpose |
|---|---|
| `on_knob_value(cc_value)` | SMK-25 knob, absolute: CC 0..127 spans the focused field's full range |
| `on_knob_delta(steps)` | fallback for endless encoders (`knobctl.knob_mode = "rel2"`) |
| `note_target_channel` | where knobctl forwards SMK-25 notes ("the keyboard plays what I'm pointed at") |
| `on_device_message(msg)` | optional: mirror hardware-originated MIDI into the GUI (devicesync) |
| `draw(state)` | ALSO drives periodic work (config flush, throttle flush) — **midicrt never calls `update()`** |

Common keys on every page: arrows = cursor / nudge (Shift = coarse), Enter =
typed entry (auto page-lock so digits reach the page), `,`/`.` MIDI channel,
`L` knob learn. The key legend is always on screen; status has its own line.

Pages: **18 LXP-1** (`*`), **19 Matrix-1000** (`(`), **20 TG77** (`)`),
**21 Bass Station** (Esc menu or `)` then `+`).

## Devices

### Lexicon LXP-1 (page 18) — `devices/lxp1.py`, `docs/lxp1-protocol.md`
- Protocol: owner's manual ch.4 (scanned PDF, OCR'd) + PC1600 template decode.
  Param edits via **packed** class `0x2n` (default; PC1600-proven) or nibblized
  `0x5n`; uni 0x8000–0xBFFF, bi 0x4000–0xBFFF (centre 0x8000).
- **No OMNI.** Pin the unit's channel: hold its MIDI button, press `c` on the
  page (note+pitchbend burst). Redo after any reset.
- **Presets = rebuilt registers 0-15** (`g`/`G` → Program Change + auto-pull).
  The unit's own ROM-preset table (param 64 / Program knob) is dead on this
  unit after battery loss (see runbook). `R` recalls registers 0-127,
  `S` stores the current state into one.
- `E` pulls the Active Setup dump into the GUI (needs jack = OUT, below).
- Knob moves on the unit (Decay/Delay/Program) mirror into the GUI live.
- Envelope-less; no graphs.

### Oberheim Matrix-1000 (page 19) — `devices/matrix1000.py` (generated from the JUCE VST header)
- Edits: NRPN (CC99=0/CC98/CC6 = value+64, **param 21 raw**) — proven on this
  unit by the JUCE VST — and/or Oberheim's edit-parameter sysex
  `F0 10 06 06 <param> <value> F7`; `settings matrix1000.edit_mode =
  both|nrpn|sysex` (default both). Mod matrix via sysex; bank select +
  unlock; Program Change; store edit buffer; `E` pulls the edit buffer
  (dump parser ported from the VST) into the GUI.
- **Throttled**: continuous edits coalesce to ≥35 ms apart, trailing value
  always sent — the M1000's CPU chokes on fast NRPN streams.
- ASCII ADSR curve beside Envelope 1-3 (web: live SVG).
- `[`/`]` groups: DCO1, Global, DCO2, VCF, VCA, Tracking, Ramps, Env1-3, LFO1-2, Mod Matrix.
- Default channel 2 ("Matrix-1k" in the rack list).

### Yamaha TG77 (page 20) — `devices/tg77.py`, `docs/tg77-port-notes.md`
- Byte-exact port of TG77ControllerVST3 (28 hand-derived tests). Frame
  `F0 43 1n 34 base layer 00 addr msb lsb F7`; layer = element_slot·0x20;
  filters dual-emit AFM+AWM. `s` slot, `f` filter bank, `d` device number,
  `x`/`v`/`X` panel Cancel/Exit/burst. Channel 6 + device number 11 are
  **guesses** — verify on the unit. Not mirrored (transmit behaviour unknown).

### Novation Bass Station Rack (page 21) — `devices/bassstation.py`
- The rack's entire MIDI surface: CC 105-107 filter, 108-112/114-118
  envelopes, 1/2/7 performance, PC 0-99 (factory names 00-39). Oscillator /
  LFO / mixer are front-panel only (hardware fact). Knob CCs mirror into the GUI.

## SMK-25 mini (BLE keyboard) — `tools/smk25-pair.sh`, `plugins/knobctl.py`
- Pair: keyboard in pairing mode → `ssh pivisualizer smk25-pair`. The script
  **always removes the old bond first** (fresh pairing-mode keys make any kept
  bond stale: symptom = "Connected: yes" but `MIDI I/O: notifications not
  enabled` in the bluetooth journal and a blinking keyboard) and pairs through
  an interactive `NoInputNoOutput` agent (one-shot `bluetoothctl pair` has no
  agent → AuthenticationFailed).
- Once bonded, bluetoothd exposes it natively as ALSA seq client `SMK25Mini`;
  knobctl's "SMK" hint grabs it. `smk25-autoconnect.service` reconnects the
  bonded keyboard every 15 s if dropped; knobctl reopens its input after 20 s
  of silence (BLE reconnects kill the subscription silently).
- Knob binding lives in `settings.json → knobctl.knob_cc` (21 on this keyboard).
  Learn (`L`) arms for 20 s only — a stale learn once grabbed the keyboard's
  CC7 volume blip and "broke" the knob.
- Keys forward to the current page's channel; the keyboard's 3-octave layer
  mode is its own setting.

## Wiring (rack, as of 2026-08-25)

```
UX16 OUT → Matrix-1000 IN → (THRU) → LXP-1 IN
UX16 IN  ← LXP-1 OUT (jack jumpered OUT)   — or Matrix OUT when pulling the Matrix
```
One interface IN jack, two units that can talk back → swap the return cable
(or add a MIDI merger). With LXP-1 THRU looped into UX16 IN, every message we
send echoes back (harmless but noisy — it swamped the sysex plugin once).

## LXP-1 runbook (battery loss, resets, rebuilds)

- **Jumper W2/W3** behind the MIDI jack: W3 = THRU (factory), **W2 = OUT**.
  OUT is required for pulls, knob mirroring, and any dump.
- **Battery loss** scrambles RAM: registers, active setup, *and the unit's
  preset table*. Symptoms over MIDI: program ID 255, params 0xFFFF; selecting
  presets via param 64 loads identical garbage.
- **Official fix** (Harman): front-panel factory reset — Plate D + Decay/Delay
  at 6 o'clock → hold MIDI while powering on → press MIDI → Hall B + knobs one
  click CW → press MIDI → Hall D → press MIDI (rapid flash) → power cycle.
  Needs working knobs.
- **MIDI-side fix** (what we did, knob broken): `tools/lxp1-rebuild-registers.py`
  writes 16 factory-LIKE setups (program ID + manual-documented Decay/Delay/FX
  values + sensible fills; `devices.lxp1.factory_like_image`) into all 128
  registers (n % 16) via Active-Setup write + store event, then verifies
  every register by recall + pull and repairs misses. Presets then live in
  registers 0-15. Lexicon no longer has factory sysex files.
- Loopback trick for remote debugging: LXP-1 THRU → UX16 IN echoes every
  byte we send, proving integrity without ears.

## External-controller mapping layer — `plugins/midimap.py`, `midimap_model.py`

The Cirklon (or any controller) addresses every parameter of every synth by
plain MIDI alone, in the background, permanently:

- **Sources**: 7-bit CC, or 14-bit NRPN (CC99/98 select, CC6 [+CC38] data),
  per channel. **Targets**: any field of any device (`lxp1 p<n>`,
  `matrix1000 n<n>` / `m<slot>.<role>`, `tg77 <scope>.<id>`, `bassstation <id>`).
  One source may fan out to several targets; a target has one source.
- **Learn**: on any page put the cursor on the field, press **`M`**, move the
  controller (20 s window). **`U`** unmaps. The header shows the focused
  field's mapping; web labels show `[ch1 cc74]` badges.
- Values scale linearly from the source range onto the field range and go
  out through the same DeviceSession encoders as the web surface (own output
  port), paced per device (Matrix 35 ms, TG77 20 ms) with trailing-value
  coalescing; the owning page mirrors the change into its shadow.
- Mappings persist in `settings.json → midimap.mappings`. Edit by hand if
  you want to lay out a whole Cirklon CC set at once.
- **Inputs**: the monitor input always; add controllers on their own
  interface via `midimap.input_hints` (e.g. `["Cirklon"]`, a second USB
  MIDI cable on the Pi). Don't list the rack-return interface there.
- Verified 2026-08-25: CC74 learned onto LXP-1 Effects Level; 0/127 became
  0x8000/0xBFFF sysex on the wire with no controller page displayed.

## Web + Android
- `http://pivisualizer.internal:8765/control` (midicrt-web-observer). POST
  mutations under `/api/manage/control/*`. Per-field slider streams are
  paced 35 ms. Playwright e2e reference: `docs/control-e2e.spec.ts`.
- Android: `mothership:~/projects/synth-controllers-android`, APK at
  `\\192.168.0.187\samba_writeable\synth-controllers-debug.apk`.
- Known gap: CRT pages and web sessions keep separate shadow state.

## Known gaps / next
- CRT ↔ web state sync; TG77 per-element-slot banks + transmit mirroring;
  TG77 channel/device verification by ear.
- **LXP-1 automation** (user intent, "another time"): the pieces exist —
  every parameter is addressable by sysex with 16-bit resolution, pulls and
  knob mirroring give state, and the web API (`/api/manage/control/set`)
  is scriptable. An automation lane would sequence timed parameter changes
  (LFO-like sweeps, scene morphs, MIDI-clock-synced moves) through the same
  paced sender; the hard parts are timing/glitch behaviour (the manual warns
  some params mute briefly when changed live) and merging with live knob
  input.
