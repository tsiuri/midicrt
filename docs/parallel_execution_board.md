# Parallel Execution Board

Track execution gates and objective evidence in this board.

| Gate | Status | Owner | Evidence (required) |
|---|---|---|---|
| G1: Ownership boundaries enforced | In Progress | Platform | `Track Boundaries` workflow run + blocked PR screenshot/log |
| G2: DeepResearch contract rollout guard enforced | In Progress | Platform + Logic | `Test Lanes / deep_research_contract_guard` run URL and `tests/test_deep_research_contract_compat.py` output summary |
| G3: Lane-sharded CI healthy | In Progress | QA | `ci_lane_summary.md` artifact from `Test Lanes` |

## G2 evidence field (required)

When posting evidence for G2, include all of the following:

- workflow run URL for `Test Lanes`
- pass/fail output for `Validate contract rollout declarations` (`python tools/check_deep_research_contract_rollout.py`)
- pass/fail output for compatibility tests (`PYTHONPATH=. pytest -q tests/test_deep_research_contract_compat.py`)
- explicit note if a major contract version change occurred in the PR and whether `tests/test_deep_research_contract_compat.py` was updated in that same PR
# Parallel Execution Board (Deep-Research Migration)

This board converts the deep-research migration/hardening targets into **parallelizable tickets** with explicit ownership and delivery constraints.

## Lane legend

- `platform` — engine/runtime contracts, scheduler, adapters.
- `logic` — analysis behavior and deterministic module outputs.
- `qa-contract` — deterministic fixtures, schema tests, regression harness.
- `observer` — web/socket observer robustness and runbook limits.

## Milestone M0 — Contract-safe hardening slices

| Milestone | Category | Ticket ID | Scope | Lane owner | Contract touch level | Max PR size | Deterministic tests required | Rollback notes required |
|---|---|---|---|---|---|---:|---|---|
| M0 | analysis module | DR-M0-AM-01 | Tempo-map metrics module emits stable jitter/lag quantiles in `modules.deepresearch` with fixed key ordering. | logic | medium | 300 LOC | Add/extend fixture test with fixed timestamp/tick vectors and exact expected quantiles. | Keep old metric keys behind compatibility shim; revert writer to prior payload if schema mismatch alarms fire. |
| M0 | analysis module | DR-M0-AM-02 | Module scheduler budget guardrails (`max_compute_ms`, skip semantics) produce deterministic status transitions (`ok/skipped/error`). | platform | high | 350 LOC | Add deterministic scheduler tests with mocked monotonic clock and explicit over-budget sequences. | Feature-flag budget guard in config (`deepresearch.enabled=false` fallback) and preserve last-good payload path. |
| M0 | widget | DR-M0-WG-01 | Deep-research summary widget contract (`views.deepresearch`) normalized for both text and pixel renderers. | platform | medium | 280 LOC | Add renderer parity test asserting same widget model snapshot for a fixed engine payload. | Keep legacy page render branch selectable via adapter toggle until parity test passes in CI. |
| M0 | adapter | DR-M0-AD-01 | Legacy event shim extraction into explicit adapter boundary (`legacy.event_shim` remains toggleable, no direct page hooks in core loop). | platform | high | 320 LOC | Add adapter contract test that replays deterministic MIDI sequence and asserts unchanged page/plugin side effects. | Single switch rollback: re-enable legacy in-core dispatch path via config gate and revert adapter registration commit. |
| M0 | adapter | DR-M0-AD-02 | Snapshot envelope normalizer for mixed schema clients (direct schema + nested envelope forms). | observer | low | 220 LOC | Add fixture-driven normalization tests with canonicalized output hashes. | Retain dual-parser fallback; if regressions appear, pin observer to old envelope parser and disable new fields. |
| M0 | analysis module | DR-M0-AM-03 | Contract version sentinel in deep-research module output (`schema_version_seen`, major mismatch error/stale behavior). | qa-contract | high | 260 LOC | Add compatibility matrix tests: current version pass, additive-field pass, major mismatch deterministic error payload. | Roll back writer-side version bump first; keep reader dual-version acceptance during rollback window. |

### M0 linked issues (create/track)

- `#DR-M0-AM-01`, `#DR-M0-AM-02`, `#DR-M0-WG-01`, `#DR-M0-AD-01`, `#DR-M0-AD-02`, `#DR-M0-AM-03`

## Milestone M1 — Observer + scale-out reliability slices

| Milestone | Category | Ticket ID | Scope | Lane owner | Contract touch level | Max PR size | Deterministic tests required | Rollback notes required |
|---|---|---|---|---|---|---:|---|---|
| M1 | analysis module | DR-M1-AM-01 | Event-triggered analysis cadence mode with deterministic debounce windows for dense MIDI bursts. | logic | medium | 300 LOC | Add cadence tests with synthetic burst fixtures and exact expected run/skip ticks. | Revert cadence policy default to `throttled_hz`; keep event-triggered mode behind config flag. |
| M1 | widget | DR-M1-WG-01 | Multi-panel deep-research widget composition (summary + diagnostics) with deterministic row budgeting at fixed terminal sizes. | logic | low | 260 LOC | Add golden widget-tree tests for `cols/rows` fixtures (80x24, 100x59) with exact node layout. | Fallback to summary-only widget if row-budget assertions fail in production snapshots. |
| M1 | adapter | DR-M1-AD-01 | Observer backpressure adapter: bounded snapshot queue + drop policy counters exposed via diagnostics. | observer | medium | 280 LOC | Add deterministic queue simulation test verifying drop counts and last-delivered sequence IDs. | Restore unbounded passthrough adapter via startup flag and emit warning-only metrics mode. |
| M1 | adapter | DR-M1-AD-02 | Reconnect-safe client adapter with monotonic sequence continuity checks and stale markers. | observer | medium | 260 LOC | Add reconnect replay tests with forced disconnect schedule and expected stale/lag flags. | Disable continuity enforcement and fall back to current reconnect strategy if false positives spike. |
| M1 | analysis module | DR-M1-AM-02 | Deterministic capture/export handoff metadata from engine to research module (`source_tick`, `produced_at`, `lag_ms`). | platform | medium | 300 LOC | Add fixed-clock integration test asserting exact metadata fields in emitted snapshots. | Revert metadata additions as optional fields only; consumers must ignore absent keys. |
| M1 | widget | DR-M1-WG-02 | Text/pixel degradation adapter docs + widget fallback states for missing optional pixel backend. | qa-contract | low | 200 LOC | Add renderer fallback tests proving identical semantic text output when pixel backend unavailable. | Keep text renderer as forced default profile; remove pixel-specific widget branches if regressions persist. |

### M1 linked issues (create/track)

- `#DR-M1-AM-01`, `#DR-M1-WG-01`, `#DR-M1-AD-01`, `#DR-M1-AD-02`, `#DR-M1-AM-02`, `#DR-M1-WG-02`

## Global acceptance rules (apply to every ticket)

1. **Determinism is mandatory**: each ticket must land at least one deterministic fixture/snapshot assertion (no wall-clock dependent flakes).
2. **Rollback notes are mandatory**: each PR must include a one-step operational rollback plus compatibility behavior during rollback.
3. **Contract declaration is mandatory**: PR body must state `contract touch level` (`none/low/medium/high`) and whether reader-first rollout is required.
4. **PR size cap is mandatory**: if scope exceeds `Max PR size`, split into sequenced slices and link dependent ticket IDs.
# Parallel Execution Board

This board tracks lane-sharded CI runtime health for required pull-request checks and nightly full-matrix runs.

## SLA

- **Target:** required CI median runtime **under 8 minutes** (< 480 seconds).
- **Source of truth:** `Test Lanes` workflow job summary + `ci-lane-summary` artifact.

## Runtime Metrics (median + p95)

| Lane | Required on PR (path-filtered) | Nightly/full-matrix coverage | Median runtime (s) | p95 runtime (s) | SLA status |
|---|---|---|---:|---:|---|
| platform | Yes | Yes (`schedule` + `workflow_dispatch`) | TBD | TBD | Track against 480s overall median target |
| logic | Yes | Yes (`schedule` + `workflow_dispatch`) | TBD | TBD | Track against 480s overall median target |
| qa-contract | Yes | Yes (`schedule` + `workflow_dispatch`) | TBD | TBD | Track against 480s overall median target |
| observer | Yes | Yes (`schedule` + `workflow_dispatch`) | TBD | TBD | Track against 480s overall median target |

## Update protocol

1. Open the latest `Test Lanes` workflow run.
2. Use the `ci-lane-summary` artifact table values for lane runtimes.
3. Update median and p95 values per lane in this table (rolling 2-week window).
4. If overall required-check median is `>= 480s`, log a remediation task before merging new CI scope.
## Gate Evidence

- **G4 — Fixture modularization complete:** PASS
  - Evidence doc: `docs/fixture_dependency_map.md`
  - Evidence details:
    - Deep-research fixtures are directory-scoped (`tests/fixtures/deep_research_sequences/*.json`) instead of a monolithic corpus file.
    - Deterministic sorted loading is centralized in `tests/deep_research_fixture_loader.py`.
    - Fixture schema validation and duplicate fixture-name detection are enforced during load.
    - Track tests consume the deterministic loader and assert fixture naming policy.
## Gates

| Gate | Description | Status | Evidence |
|---|---|---|---|
| G1 | Ownership boundary CI guard blocks cross-track PRs without explicit override. | ✅ Active | `track-boundaries` required workflow + invalid fixture proof in CI job (`.ci/fixtures/invalid_cross_track_files.txt`). |

## Gate G1 archived evidence

- Added deterministic failing fixture: `.ci/fixtures/invalid_cross_track_files.txt`.
- Added deterministic passing fixture: `.ci/fixtures/valid_track_a_only_files.txt`.
- Workflow step **"Verify fixture - mixed Track A + Track B sample fails"** in `.github/workflows/track-boundaries.yml` asserts that the checker returns non-zero for the invalid mixed ownership sample.
- Workflow step **"Verify fixture - valid Track A only sample passes"** confirms the checker still allows single-track edits.

## Branch protection requirement

Repository admins must set branch protection on `master` to require the status check named:

- `enforce-track-boundaries`

Without branch protection requiring this check, G1 is not considered enforced.
