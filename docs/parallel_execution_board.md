# Parallel Multi-Agent Execution Board

This board operationalizes the readiness criteria in:

- `docs/parallel_readiness_checklist.md`
- `docs/parallel_dev_playbook.md`
- `docs/contributor_tracks.md`

Use it as the source of truth for owners, target dates, and gate status while moving from pilot to full multi-agent delivery.

## Status legend

- `NOT_STARTED`: no implementation merged
- `IN_PROGRESS`: implementation underway but not yet enforced in required CI
- `BLOCKED`: work is waiting on prerequisite dependencies
- `READY_FOR_VALIDATION`: implementation merged; waiting for proof/evidence
- `DONE`: gate complete with objective evidence links

## Parallel readiness gate board

| Gate ID | Gate | Owner lane | DRI | Start | Target | Dependency | Current status | Evidence / tracking link |
|---|---|---|---|---|---|---|---|---|
| G1 | Ownership CI guard active | platform + qa-contract | runtime/ops | 2026-02-26 | 2026-02-28 | none | IN_PROGRESS | Add required status check + blocked test PR evidence |
| G2 | Contract-version protocol active | platform + qa-contract | core/engine | 2026-02-26 | 2026-03-01 | G1 | IN_PROGRESS | Contract check job + compatibility test report |
| G3 | Lane-sharded CI active | qa-contract | qa/infrastructure | 2026-02-27 | 2026-03-02 | G1 | NOT_STARTED | Per-lane workflow summary + timing report |
| G4 | Fixture modularization complete | logic + qa-contract | qa/infrastructure | 2026-02-28 | 2026-03-03 | G2 | NOT_STARTED | Fixture dependency map + deterministic loader test |
| G5 | PR template lane metadata mandatory | platform + qa-contract | runtime/ops | 2026-02-27 | 2026-03-01 | G1 | IN_PROGRESS | PR template validation check required in CI |

## Milestone plan

| Milestone | Window | Exit criteria | Owner lane(s) | Status |
|---|---|---|---|---|
| M0: Gate implementation | 2026-02-26 → 2026-03-03 | G1–G5 reach `READY_FOR_VALIDATION` or `DONE` | platform, logic, qa-contract | IN_PROGRESS |
| M1: 1-week pilot (2–3 agents) | 2026-03-04 → 2026-03-10 | Pilot report generated with conflict/CI/contract metrics | all lanes | NOT_STARTED |
| M2: Go/No-Go decision | 2026-03-11 | All checklist gates `DONE` and thresholds pass | runtime/ops + leads | NOT_STARTED |
| M3: Full multi-agent rollout | 2026-03-12+ | Scale-up trigger satisfied and on-call rotation staffed | all lanes | NOT_STARTED |

## Live metrics scoreboard (rolling 2-week window)

| Metric | Target | Current | Source | Status |
|---|---:|---:|---|---|
| Cross-lane conflict rate | < 10% | TBD | merged PR audit log | BLOCKED (pilot not started) |
| Median required CI duration | < 8 min | TBD | CI dashboard export | BLOCKED (lane sharding not active) |
| Unreviewed contract-breaking merges | 0 | TBD | contract-review audit | BLOCKED (contract check not enforced) |

## Immediate next actions (next 5 working days)

1. `platform`: land/enable required CI gate for track ownership boundary enforcement (G1).
2. `qa-contract`: add metadata validation check for PR lane + `contract-impact` fields (G5).
3. `platform + qa-contract`: wire contract-version compatibility checks into required CI (G2).
4. `qa-contract`: split required CI into lane-scoped jobs and publish per-lane timing baseline (G3).
5. `logic + qa-contract`: modularize deep-research fixtures and add fixture dependency map artifact (G4).

## Update protocol

- Update this board at least once per day during M0 and pilot week.
- Any gate status change to `DONE` must include at least one objective evidence link.
- If a target date slips by >1 day, add a brief blocker note under the corresponding gate in the PR that updates this file.
