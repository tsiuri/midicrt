# Lexicon LXP-1 MIDI protocol — reverse-engineering notes

Sources, in order of authority:

1. **LXP-1 Owner's Manual rev 1.2** (Lexicon part 070-06023), chapter 4 "MIDI Sys Ex
   implementation data" — scanned PDF fetched from lexiconpro.com, OCR'd by hand
   2026-08-24. This gave us the *complete documented* implementation.
2. Karl Mousseau's Peavey PC1600 template (`1600xLxp1.syx` from
   defectiverecords.com/pc1600) — denibblized and decoded 2026-08-24; confirmed the
   wire format empirically but used only the crude "packed parameter adjust" message.

## Identity

All messages: `F0 06 02 <cmd|chan> ... F7`

- `06` = Lexicon manufacturer ID
- `02` = LXP-1 product ID
- byte 4 high nibble = message class, low nibble `n` = MIDI channel 0-15

## Message classes (byte 4)

| byte 4 | class | direction |
|--------|-------|-----------|
| `0n`   | Active Setup Data dump (56 packed bytes + sumcheck) | rx/tx |
| `1n`   | Stored (single register) data: `1n <reg> 38 <56 packed> <sum> F7` | rx/tx |
| `2n`   | Packed Parameter Adjust: `2n <param> <3-byte packed 16-bit> F7` | rx/tx |
| `3n`   | Requests: `3n <event> <p> F7` | rx |
| `4n`   | All Registers Data (128×56 packed + sumcheck) | rx/tx |
| `5n`   | **Nibblized Parameter Adjust**: `5n <param> <d15-12> <d11-8> <d7-4> <d3-0> F7` | rx/tx |
| `6n`   | Events: `6n <event> <p> F7` — `70`=store register p, `71`=recall register p | rx |

Request event codes (class `3n`): `60`=active setup, `61`=one register (p=reg#),
`62`=packed param (p=param#), `64`=all registers, `65`=nibble param (p=param#).

All parameter adjustments require the full 16 bits; for 8-bit parameters pad the
MS byte with zeros. **We use the nibblized form (`5n`) exclusively** — trivial to
build, full resolution. (The PC1600 template used `2n` with the fader byte packed
into the *high* byte, which clamps at 0xBFFF ≈ fader 96 — one reason to do better.)

## Parameter numbering (Parameter Map)

| Param | Size | Meaning |
|-------|------|---------|
| 0     | 16-bit | Decay-knob parameter (per-algorithm, see tables) |
| 1     | 16-bit | Delay-knob parameter (per-algorithm) |
| 2-9   | 16-bit | Other microcode parameters (per-algorithm) |
| 10    | 16-bit | Processor Input Level (not stored in registers; recall sets it to max) |
| 32-47 | 8-bit  | Name (16 chars) |
| 48-51 | 8-bit  | MIDI patch sources 0-127 (Table 1: 0-31 CC, 32-63 switches, 64 last note, 65 velocity, 66 aftertouch, 67 pitch bend, 68 MIDI tempo) |
| 52-55 | 8-bit  | MIDI patch destinations (ucode param nums 0-9) |
| 56-59 | 8-bit  | MIDI scale factors, -127..+127 2's complement |
| 60-63 | 16-bit | MIDI patch offsets (internally recomputed; write-mostly useless) |
| 64    | 8-bit  | **Setup number**: 0-127 = registers, 128-144 = factory presets 0-15 |
| 65    | 8-bit  | **Program (algorithm) ID** 1-8 |

## Value ranges (16-bit microcode params 0-10)

- Unipolar: `0x8000` (min) → `0xBFFF` (max)
- Bipolar: `0x4000` (most negative) → `0x7FFF` (least negative), `0x8000` = zero,
  → `0xBFFF` (most positive)
- Out-of-range values are clamped by the unit.
- Bass Multiply is special: `0x4000` = min, `0x8000` = ×1.0, `0xBFFF` = max.
- Chorus-1 flange depth: values ≤ `0x8200` "not installed"; delays above `0xBD00`
  clamp to 32000 samples (1 s).

## Algorithms (Program IDs)

1 Rooms and Halls · 2 Plates · 3 Chorus 1 (Stereo Flange) · 4 Delay 2 (4-tap
bounce) · 5 Chorus 2 (Chromatic Resonator) · 6 Inverse · 7 Gate · 8 Delay 1
(6-voice Chorus & Echo)

Per-algorithm parameter tables (name / param# / uni-bi / steps / display range)
are encoded in `pages/lxp1.py` (`ALGORITHMS`) straight from manual pages 4-11 …
4-22; that file is the machine-readable source of truth.

## Data packing (dumps)

Dumps use 7-in-8 packing: 7 data bytes → 8 wire bytes, MSBs collected into the
first byte right-justified (`0 g7 f7 e7 d7 c7 b7 a7`, then `0 a6..a0` …). Short
final packet: MSB byte first, active MSBs right-justified. Sumcheck = sum over
packed bytes, 7-bit. Register/setup payload = 56 packed = 49 unpacked bytes:
prog ID (1), knob+microcode params 0-9 as 16-bit LE pairs (20), name (16),
patch sources (4), patch dests (4), scale factors (4).

## What remains un-verified (candidate capture work later)

- Exact display-value tapers (Hz scales, delay-step exponential curves) — the page
  shows interpolated approximations marked `~`.
- Whether standard MIDI Program Change selects setups (likely; chart page not OCR'd).
- Dump parsing round-trip against the real unit (active setup / register / all-registers)
  — needed for a future "pull state into the page" feature and for the sysex librarian.
- Dynamic-MIDI patch rows (48-63) behavior when written over MIDI.
- Front-panel knob transmit (packed class `2n`) — could be used for bidirectional sync.
