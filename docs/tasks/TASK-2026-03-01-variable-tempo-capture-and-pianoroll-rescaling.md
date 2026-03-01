# TASK-2026-03-01 variable tempo capture and piano roll rescaling

## Goal

Add first-class support for BPM changes during capture and make piano-roll rendering visibly rescale against the current tempo.

## User-required visual behavior

Desired outcome for piano roll:

- If current BPM is reduced, notes from before that reduction should look squished.
- If current BPM is increased, notes from before that increase should look stretched.

This is intentionally a relative-to-current-tempo visualization, not a fixed beat-grid-only view.

## Current architecture notes

- `SessionHeader` already has `tempo_segments` and `time_signature_segments`.
- MIDI import/export already reads/writes tempo/time-signature meta events.
- Live memory capture currently starts a session with one BPM value and does not append tempo segments during run.
- Session index duration currently uses a single BPM scalar and is inaccurate for variable-tempo sessions.

Primary files:

- `engine/memory/session_model.py`
- `engine/memory/capture.py`
- `engine/core.py`
- `engine/memory/midi_io.py`
- `engine/memory/storage.py`
- `pages/pianoroll.py`
- `pages/pianoroll_exp.py`
- `fb/compositor_renderer.py`

## Proposed architecture

1. Capture tempo as timeline segments.

- Add segment appends during live capture when effective BPM changes past hysteresis.
- Add time-signature segment appends on meter changes with confidence gating.
- Keep ticks as canonical storage for all events/spans.

2. Shared tempo conversion utility.

- Add a `TempoTimeline` helper in `engine/memory`:
  - `tick_to_seconds(tick)`
  - `seconds_to_tick(seconds, bpm_ref)` or equivalent helper for projection
  - Piecewise integration across tempo segments.

3. Two projection modes for piano roll.

- Beat-space mode (existing behavior): fixed ticks-per-column.
- Tempo-relative mode (new target behavior):
  - Compute real elapsed seconds for event positions via tempo timeline.
  - Reproject those seconds into current-tempo-equivalent tick distance:
    - `ticks_equiv = elapsed_seconds * (current_bpm * PPQN / 60)`
  - This produces the required squish/stretch effect relative to current BPM.

4. Metadata and indexing updates.

- Replace scalar-BPM duration estimation in indexing with segment-integrated duration.
- Preserve backward compatibility when no tempo segments exist (fallback to header BPM).

## Implementation phases

1. Phase A: capture segments

- Wire segment updates in `MemoryCaptureManager.on_transport(...)` via `engine.core` transport snapshots.
- Add duplicate suppression and hysteresis (minimum delta BPM + minimum tick spacing).

2. Phase B: tempo timeline utility

- Implement and unit-test piecewise conversion functions.
- Add regression tests for monotonicity and edge ticks.

3. Phase C: piano roll tempo-relative projection

- Add an opt-in mode flag in config/page state.
- Implement projection in page 8/16 builders.
- Keep compositor-facing payload shape stable where possible.

4. Phase D: index/export/replay checks

- Update index duration logic to use segments.
- Verify export meta events are complete and ordered.
- Verify replay behavior remains transport-tick stable.

## Acceptance criteria

- Mid-capture tempo changes are stored as ordered tempo segments.
- Exported MIDI contains matching `set_tempo` events at expected ticks.
- Index/session duration in seconds is accurate for variable tempo recordings.
- In tempo-relative piano-roll mode:
  - decreasing current BPM visibly squishes earlier notes,
  - increasing current BPM visibly stretches earlier notes.
- Existing beat-space behavior remains available and stable.

## Notes for agents

- Do not break existing page payload contracts unless absolutely required; if changed, update both renderer and tests in same change.
- Keep renderer cache behavior consistent with time-based animations (avoid freezing effects via stale cache keys).
- Add focused tests first for conversion math, then projection snapshots.
