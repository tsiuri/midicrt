# Parallel Execution Board

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
