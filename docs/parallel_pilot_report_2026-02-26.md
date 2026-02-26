# Parallel Pilot Report (Week Simulation) — 2026-02-26

## Scope

This report executes the 1-week pilot framework in `docs/parallel_readiness_checklist.md` using:

- 3 simultaneous simulated contributors with policy-compliant branch names (`agent/<lane>/<ticket>-<slug>`),
- lane-sharded test execution under local runtime parity,
- governance gate inspection + malformed-PR dry-run checks.

## Pilot execution evidence

### Contributor concurrency + lane protocol

A compressed 5-day simulation was run with 17 PR-like branch events (3/day for 5 days + 2 conflict-drill branches), using lanes `platform`, `logic`, and `qa-contract` and branch names matching required policy.

- Total PR-like events: **17**
- Cross-lane merge conflicts observed: **1**
- Cross-lane conflict rate: **5.88%**

Sample branches from execution log:

- `agent/qa-contract/1011-day1-a1`
- `agent/platform/1012-day1-a2`
- `agent/logic/1013-day1-a3`
- `agent/platform/1021-day2-a1`
- `agent/logic/1022-day2-a2`
- `agent/qa-contract/1023-day2-a3`

### CI latency sampling (lane-sharded)

Lane commands were executed 5 times each (15 total samples):

- `track_a_tests`: `tests/test_scheduler_overload.py tests/test_schema_contract.py tests/test_deep_research_replay_contracts.py`
- `track_b_tests`: `tests/test_deep_research_tracks.py tests/test_engine_replay_determinism.py`
- `integration_observer_tests`: `tests/test_web_observer_bridge.py tests/test_ipc_pubsub.py`

Measured result set:

- Median CI runtime: **0.591s**
- p95 CI runtime: **1.133s**

### Contract governance + malformed-PR checks

Required malformed-PR checks from the readiness plan:

1. Missing lane metadata PR should hard-fail.
2. Contract-breaking change without required contract review should hard-fail.

Observed state:

- PR template includes lane metadata fields, but no required CI validator enforces metadata presence/validity.
- No required contract-version/contract-review enforcement job is present in workflows.
- CODEOWNERS policy file is absent.
- Track-boundary script exists, but in this local environment it could not be executed end-to-end against `origin/master...HEAD` because no `origin` remote is configured.

## Gate status (from checklist)

| Gate | Status | Evidence |
|---|---|---|
| Ownership CI guard active | ❌ FAIL | No `.github/CODEOWNERS`; no branch-protection evidence in-repo. |
| Contract-version protocol active | ❌ FAIL | No contract-version CI job in `.github/workflows/`. |
| Lane-sharded CI active | ✅ PASS | `.github/workflows/test-lanes.yml` has per-lane jobs + summary artifact. |
| Fixture modularization complete | ⚠️ PARTIAL | Track-level fixture split exists; full fixture dependency map evidence not committed. |
| PR template lane metadata mandatory | ❌ FAIL | Template fields exist, but no metadata-validation CI check. |

## Threshold decision

| Metric | Threshold | Observed | Result |
|---|---:|---:|---|
| Cross-lane conflict rate | < 10% | 5.88% | ✅ PASS |
| Median CI duration | < 8 minutes | 0.591s | ✅ PASS |
| Unreviewed contract-breaking merges | 0 | 2 pilot governance violations (missing enforcement) | ❌ FAIL |

## Incident log

| ID | Severity | Day | Incident | Impact | Resolution |
|---|---|---:|---|---|---|
| INC-PP-001 | Sev-2 | 3 | Cross-lane merge conflict (`platform` vs `logic`) on same playbook hunk | Manual conflict resolution required | Captured in conflict drill, no production outage |
| INC-PP-002 | Sev-2 | 5 | Missing hard-fail for lane metadata validation | Non-compliant PR could merge | Remediation backlog item RB-001 |
| INC-PP-003 | Sev-2 | 5 | Missing hard-fail for contract-breaking governance | Contract-risking PR could merge without proper review | Remediation backlog item RB-002 |

No Severity-1 coordination incidents were observed.

## Formal decision

**Decision: NO-GO**

Rationale: measurable threshold for contract governance (zero unreviewed contract-breaking merges) is not currently enforceable and fails pilot criteria; multiple required gates are not active.

## Remediation backlog (required for NO-GO)

| ID | Remediation | Owner | Target date | Status |
|---|---|---|---|---|
| RB-001 | Add required CI check to validate PR template lane metadata and branch naming policy (`agent/<lane>/<ticket>-<slug>`) | DevEx / QA-Contract | 2026-03-01 | Planned |
| RB-002 | Implement contract-version governance workflow with required reviewer/approval guard for breaking contract changes | Platform + QA-Contract | 2026-03-03 | Planned |
| RB-003 | Add and enforce `.github/CODEOWNERS` for lane ownership paths and required review routing | Platform / Repo Admin | 2026-03-02 | Planned |
| RB-004 | Commit fixture dependency map proving lane-scoped fixture modularization and cross-lane import boundaries | QA-Contract | 2026-03-04 | Planned |
| RB-005 | Configure/prove branch protection: required checks for `track-boundaries`, lane tests, metadata validator, contract-version validator | Repo Admin | 2026-03-05 | Planned |

