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
