# TG77 port notes — VST → devices/tg77.py

Ported 2026-08-24 from the JUCE VST "TG77ControllerVST3" (`tg77processor.cpp`
+ `TG77ParameterDefinitions.h`, kept in the session scratchpad at port time).
The VST is the protocol authority; `tests/test_device_tg77.py` hard-asserts
hand-derived byte sequences against it.

## Frame format

Full wire frame, 11 bytes (`addLiteralMessage`, tg77processor.cpp:307-331):

```
F0 43 (0x10|dev) 34 <base> <layer> 00 <address> <msb> <lsb> F7
```

`message()` returns the 9-byte body between F0/F7 (mido-style sysex data):
`(0x43, 0x10|dev, 0x34, base, layer, 0x00, address, msb, lsb)`.
Device number is 0-15 (VST default choice index 11 → `DEFAULT_DEVICE_NUMBER`).
Every payload byte is masked `& 0xFF` mirroring the C++ `uint8_t` casts.

## Base / layer byte semantics

From tg77processor.cpp:

| Formula | Source | Value |
|---|---|---|
| AFM layer byte | `getAfmLayerByte()` (line 407) | `element_slot * 0x20` (slot 0-3 → 0x00/0x20/0x40/0x60) |
| Slot filter base byte | `getSlotFilterBaseByte()` (line 412) | `element_slot * 0x20` (identical formula, kept separate as in the C++) |

Bases and layers per message family:

| Family | Base | Layer |
|---|---|---|
| Global (AFM main/sub LFO) | 0x05 | afm layer byte |
| AFM operator | per-op `OPERATOR_INFOS` base (OP1..OP6 = 0x56/0x46/0x36/0x26/0x16/0x06) | afm layer byte |
| AWM element | 0x07 | afm layer byte — EXCEPT `awmPitchEgVelocity`: literal 0x00 (line 958) |
| Filter (bank) | 0x09 | slot base + filter offset: AFM = `filter_select` (0/1), AWM = `filter_select + 3` (line 750-751) |
| Filter (common) | 0x09 | slot base + 0x02 (AFM) / + 0x05 (AWM) (lines 381, 388) |
| Setup | 0x02 (cutoff control src/amt), 0x0F (bulk protect) | literal 0x00 |
| Front panel | 0x0D | literal 0x00 |

Every filter parameter emits TWO messages (AFM variant then AWM variant),
exactly as the VST does. Filter-common params ignore `filter_select`.

## Packed-field catalog

| Spec ids | Address | Packing | Notes |
|---|---|---|---|
| `RateScalingSign`+`RateScalingValue` (operator) | 0x0F | `sign*8 + (7 - value)` | value INVERTED — unique to the operator variant (line 1120) |
| `filterEgRateScalingSign`+`Value` | 0x10 | `value + sign*8` | no inversion (line 799) |
| `awmPitchEgRateScalingSign`+`Value` | 0x10 | `sign*8 + value` | (line 947) |
| `awmAmpEgRateScalingSign`+`Value` | 0x57 | `sign*8 + value` | (line 1010) |
| `DetuneSign`+`DetuneValue` (operator) | 0x1A | `(sign ? 0x10 : 0x00) + value` | (line 1206) |
| `Mode`+`PitchMod` (operator) | 0x18 | `pitch_mod*4 + 2 + mode` | shares address 0x18 with PitchEgSwitch (line 1190) |
| `PitchEgSwitch` (operator) | 0x18 | whole-byte LITERAL: `pitch_eg_off/on` from `OPERATOR_INFOS` | per-op values (OP1 09/0B, OP2 1D/1F, OP3+OP4 08/0A, OP5 1C/1E, OP6 09/0B) — these literals presumably bake in the neighboring mode/pitch-mod bit state for each operator; ported verbatim, not decoded (lines 1076-1084) |
| `PhaseSync`+`InitialPhase` (operator) | 0x19 | msb = sync (0/1), lsb = phase | the only packing across msb/lsb (line 1223) |
| `KeyOnVelSign`+`KeyOnVel` (operator) | 0x11 | `(sign ? 0x08 : 0x00) + value` | (line 1239) |
| `filterEgKeyOnVelocitySign`+`Velocity` | 0x33 (common) | `value + sign*8` | (line 843) |
| `filterLfoCutoffSensitivitySign`+`Sensitivity` | 0x34 (common) | `value + sign*8` | (line 854) |
| `awmKeyOnVelocitySign`+`Velocity` | 0x60 | `sign*8 + value` | (line 1045) |
| `awmAmpModSign`+`AmpMod` | 0x62 | `sign*8 + value` | (line 1058) |

## Value transforms (non-packed)

- **Inverted 63**: `63 - v` — all 14 operator AFM EG rates/levels
  (note the address shuffle: HT=0x0D, R1-R4=0x00-0x03, RR1/RR2=0x04/0x05,
  L0=0x0E, L1-L4=0x06-0x09, RL1/RL2=0x0A/0x0B) and AWM Amp EG
  HT/R1,R2,R3,R4,RR (0x50-0x54).
- **Inverted 255 + two-byte**: operator `Bp1-4Amount` (0x20-0x23):
  `v' = 255 - v`, msb=`v'//128`, lsb=`v'%128`.
- **Plain two-byte (no inversion)**: `filterEgBp1-4Amount` (0x15-0x18),
  `awmOutputLevelScalingBp1-4Amount` (0x5C-0x5F), and `awmWave`
  (0x01, after `v-1`).
- **Signed-7 (`v + 64`)**: `filterEgL0-L4/RL1/RL2` (0x09-0x0F),
  `awmWaveFixedPitchFine` (0x04).
- **Minus-one**: `SusLoopPoint` (0x0C), `Wave` (0x17), `awmWave`.
- **`127 - v`**: operator `OutputLevel` (0x1B).
- **awmMainLfoWave skip**: wire value increments by 1 for choices >= 4
  (wire 4 unused; Sine→5, Sample&Hold→6) (lines 973-975).

## Front-panel buttons

- Cancel = `0D 00 / addr 0x16 / 00 40`; Exit = `0D 00 / addr 0x18 / 00 40`.
- Cancel burst = 3× Cancel then 3× Exit, 35 ms apart, per
  `cancelBurstDelaySeconds`/`cancelBurstRepeatsPerMessage` (lines 10-11,
  612-679). The Python `panel_cancel_burst()` returns the 6 messages in
  order; TIMING IS THE CALLER'S JOB (the constants are exported).

## Ambiguities / faithfully-ported oddities

1. **`awmPitchEgVelocity` layer 0x00** (line 958): every other AWM param uses
   the element layer byte; this one is hard-coded `0x00`. Possibly a VST bug
   (would only address element 1), but ported verbatim.
2. **Address 0x18 double duty** (operator): `PitchEgSwitch` writes per-op
   literals from `operatorInfos` while `Mode`+`PitchMod` write
   `pitch_mod*4 + 2 + mode` to the same address. The VST never reconciles
   the two — last write wins on the device. The literal tables were not
   decoded into bit fields; they are carried as opaque bytes.
3. **Operator rate-scaling inverts the value (`7 - v`)** while the filter and
   both AWM rate-scaling packings do not. Confirmed against the C++ (line
   1120 vs 799/947/1010); intentional asymmetry, not a porting error.
4. **`getSlotFilterBaseByte()` duplicates `getAfmLayerByte()`** — both are
   `slot * 0x20`. Kept as two functions to preserve intent.
5. **`sendIfChanged` de-duplication** (only send on value change) is host
   behavior, NOT ported — the Python encoders are pure and always return the
   message(s). Same for the element/filter bank save/restore machinery
   (lines 458-591): that is VST UI state management, not protocol.
6. **DEFAULT_CHANNEL = 6** ("Yamaha 1" in the rack instruments list) is a
   GUESS to verify against the hardware; the VST has no MIDI-channel concept
   for these sysex messages (device number 0-15 is the addressing mechanism,
   default 11).

## Nothing skipped

Every dispatch entry in `sendGlobalParameters`, `sendFilterParameters`,
`sendSetupParameters`, `sendAwmParameters`, `sendOperatorParameters`, and
`sendLiteralActionMessages` has a corresponding `SENDERS`/`PANEL_ACTIONS`
entry (a test asserts total spec-id coverage). No mapping was left out or
approximated.
