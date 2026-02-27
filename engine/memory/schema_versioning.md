# Session Memory Schema Versioning

This document defines the canonical in-memory session schema used by piano-roll memory capture and any future session-aware modules.

## Canonical schema ID

- `schema_name`: `midicrt.session`
- `schema_version`: `1.0.0`

Writers must set both fields in every `SessionModel` instance.

## Session header

`SessionHeader` is required and includes:

- `session_id`: stable unique ID for one captured run.
- `start_tick`: first transport tick for the session.
- `stop_tick`: finalized transport tick (updated on stop/flush).
- `ppqn`: pulses-per-quarter-note for the captured clock domain.
- `bpm`: baseline tempo for export/interpretation.
- `tempo_segments` (optional): piecewise tempo map (`start_tick`, `bpm`).
- `time_signature_segments` (optional): piecewise meter map (`start_tick`, `numerator`, `denominator`).

## Event stream contract

`SessionModel.events` is an ordered normalized stream of `MidiEvent` items.

Rules:

1. Every event carries `tick` and `seq`.
2. `seq` is monotonic and unique per session.
3. Sorting by `(tick, seq)` provides stable same-tick ordering.
4. Event `kind` currently supports:
   - `note_on`, `note_off`, `control_change`, `program_change`
   - extensible: `pitch_bend`, `channel_pressure`, `poly_aftertouch`

## Raw + derived data

The schema intentionally stores both:

- Raw normalized event stream (`events`) for deterministic replay/export.
- Derived note spans (`note_spans`) for visualization/editing.

Derived spans must never replace raw events.

## Active-note closeout rules

Implementations must close active notes under these conditions:

1. `note_on` with velocity `0` behaves as `note_off`.
2. CC123 (All Notes Off) closes all active notes on that channel.
3. Stop/finalize flush closes all remaining active notes at `stop_tick`.

When closeout is synthesized (for cleanup), writers may emit synthesized `note_off` events with `source="synth"` while preserving original input events.

## Compatibility policy

- **Minor/patch (`1.x.y`)**: additive changes only (optional fields, new event kinds).
- **Major (`2.0.0+`)**: breaking shape/semantic changes.

For major upgrades, use staged rollout:

1. Readers accept old+new versions.
2. Validate mixed-version behavior.
3. Switch writers.
4. Remove legacy parsing later.
