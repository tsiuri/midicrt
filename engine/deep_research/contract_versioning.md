# Deep Research Contract Versioning

This document defines staged-change rules for `ResearchContract`.

## Version format

- Contract versions use `MAJOR.MINOR` (for example: `1.0`).
- `MAJOR` changes indicate potentially breaking shape changes.
- `MINOR` changes indicate additive evolution only.

## Compatibility policy

- Additive fields are allowed in minor-stage updates.
  - Examples: adding optional top-level contract keys, adding optional nested fields.
  - Existing required fields and value semantics must remain stable.
- Breaking shape changes require a staged rollout.
  - Examples: removing/renaming required fields, changing required field types, changing incompatible payload structure.

## Required staged rollout for breaking changes

1. **Prepare readers first**
   - Land compatibility readers that support both the old and new shapes.
   - Keep writers on the old shape.
2. **Dual-read validation phase**
   - Run in production with mixed agents/versions and validate deterministic behavior.
   - Ensure tests cover old writer/new reader and new writer/new reader paths.
3. **Writer switch phase**
   - Flip writers to emit the new major contract only after all readers are upgraded.
4. **Cleanup phase**
   - Remove old-shape compatibility code in a later follow-up after stability window.

## Cross-track override precedence

For cross-track edits, `tools/check_track_boundaries.py` accepts three override channels.
When multiple are present, precedence is:

1. `ALLOW_CROSS_TRACK=1` (explicit env override).
2. PR label `allow-cross-track` (CI maps this to env).
3. Repository marker file `.ci/allow_cross_track`.

Concrete examples:

- **Example 1 (env wins):** `ALLOW_CROSS_TRACK=1` is set and `.ci/allow_cross_track` exists. The effective source is `ALLOW_CROSS_TRACK`.
- **Example 2 (label used):** CI sets `ALLOW_CROSS_TRACK_LABEL=1` from a PR label and env override is absent. Effective source is `label:allow-cross-track`.
- **Example 3 (file fallback):** no env/label override but `.ci/allow_cross_track` exists. Effective source is `.ci/allow_cross_track`.
- **Example 4 (no override):** none of the above are present; mixed Track A + Track B edits fail.

## Parallel-agent safety

When multiple agents may run concurrently, always assume temporary version skew.
Use major-version compatibility checks to fail fast with deterministic error payloads,
rather than partially parsing unknown contract shapes.

## Snapshot envelope migration example (`schema_version: 4` → `5`)

### Before (legacy/older mixed envelope)

```json
{
  "type": "snapshot",
  "payload": {
    "schema": {
      "schema_version": 4,
      "timestamp": 1700000000.0,
      "transport": {
        "tick": 120,
        "bar": 8,
        "running": true,
        "bpm": 124.0,
        "clock_interval_ms": 20.16,
        "jitter_rms": 0.73
      },
      "module_outputs": {}
    },
    "deep_research": {
      "version": 3,
      "result": {"signature": "ok"}
    }
  }
}
```

### After (current canonical schema payload)

```json
{
  "type": "snapshot",
  "payload": {
    "schema_version": 5,
    "timestamp": 1700000000.0,
    "transport": {
      "tick": 120,
      "bar": 8,
      "running": true,
      "bpm": 124.0,
      "clock_interval_ms": 20.16,
      "jitter_rms": 0.73,
      "quality": {
        "clock_jitter_rms": 0.73,
        "clock_jitter_p95": 1.81,
        "clock_drift_ppm": -12.4
      },
      "microtiming": {
        "bins": {"early": 3, "ontime": 9, "late": 2},
        "window_events": 14,
        "window_bars": 2.0
      }
    },
    "retrospective_capture": {
      "buffer_bars": 4,
      "events_buffered": 87,
      "armed": false,
      "last_commit_path": "captures/take_001.mid"
    },
    "module_health": {
      "status": "ok",
      "updated_at": 1700000000.0,
      "modules": {"research": {"status": "ok", "latency_ms": 4.5}}
    },
    "deep_research": {
      "version": 3,
      "result": {"signature": "ok"}
    }
  }
}
```

### Migration notes

- Readers must support both top-level schema payloads and legacy wrapped payloads under `payload.schema`.
- Older envelopes may place `deep_research`, `module_health`, and `retrospective_capture` alongside `payload.schema`; compatibility readers should merge these sections when absent from the schema body.
- New transport quality and microtiming sections are additive and must default to safe empty/zero values when omitted.
- Writer-side switch to `schema_version: 5` is allowed only after dual-read compatibility tests pass for old and new envelopes.
