# Merge Reconciliation — 2026-02-27

## Scope
Source-of-truth comparison between documented claims and current repository enforcement artifacts:
- `docs/parallel_execution_board.md`
- `docs/parallel_pilot_report_2026-02-26.md`
- `.github/workflows/track-boundaries.yml`
- `.github/workflows/test-lanes.yml`
- `.github/pull_request_template.md`

## Checklist
- [x] Reviewed all claims in the two readiness docs that refer to workflow/template enforcement.
- [x] Verified each claim against `.github/workflows/*` and `.github/pull_request_template.md`.
- [x] Logged all mismatches with claim text, actual state, decision, and owner lane.
- [x] Opened one follow-up task per mismatch.
- [x] Linked mismatch tasks from `docs/parallel_execution_board.md` under an open-conflicts section.

## Mismatch ledger

| ID | Source claim text | Actual repository state | Decision | Owner lane | Follow-up task |
|---|---|---|---|---|---|
| MR-01 | "WB-002 Contract-version governance check ... Required contract-version CI gate not present." (`docs/parallel_execution_board.md`) | `.github/workflows/test-lanes.yml` already defines `deep_research_contract_guard` and `version_compatibility_tests` jobs, so a contract-version gate exists in-repo. | update docs | platform | [TASK-2026-02-27-01](docs/tasks/TASK-2026-02-27-contract-gate-doc-correction.md) |
| MR-02 | "Contract-version protocol active ❌ FAIL — No contract-version enforcement workflow in .github/workflows/." (`docs/parallel_pilot_report_2026-02-26.md`) | Contract-version workflow logic exists, but lane workflow wiring is inconsistent (`track_a_tests` empty; undefined `needs`: `track_b_tests`, `integration_observer_tests`; condition references non-exported outputs `track_a`/`track_b`). | implement missing gate | platform | [TASK-2026-02-27-02](docs/tasks/TASK-2026-02-27-fix-lane-workflow-dependencies.md) |
| MR-03 | "Lane-sharded CI active ✅ PASS" (`docs/parallel_pilot_report_2026-02-26.md`) | Lane jobs are present, but the workflow graph is internally inconsistent (undefined dependencies + placeholder job), so "active" is overstated until workflow is repaired. | implement missing gate | platform | [TASK-2026-02-27-02](docs/tasks/TASK-2026-02-27-fix-lane-workflow-dependencies.md) |

## Notes on non-mismatches reviewed
- PR metadata fields for lane/branch/ticket are present in `.github/pull_request_template.md`.
- No standalone CI validator for PR metadata presence/validity is found in current workflows; readiness docs that call this out as missing remain consistent with repo state.
