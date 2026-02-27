# Parallel Multi-Agent Readiness Checklist (Go/No-Go)

Use this checklist before scaling from a small pilot to full multi-agent delivery.

## Go/No-Go gates (all required)

Mark each gate as complete only when objective evidence exists (CI logs, audit reports, or repo policy checks).

- [ ] **Ownership CI guard active**
  - Enforced CODEOWNERS/ownership path validation must run in required CI.
  - PRs touching owned paths must be blocked unless the matching owner review requirement is satisfied.
  - Evidence: branch protection + required status check + at least one blocked test PR proving guard behavior.
  - Implementation target: required check `enforce-track-boundaries` from `.github/workflows/track-boundaries.yml`; archived in `docs/parallel_execution_board.md` (Gate G1).

- [ ] **Contract-version protocol active**
  - Contract changes must follow versioning policy (additive = minor, breaking = major).
  - CI must verify version declarations and compatibility tests for reader/writer rollout order.
  - Required check behavior (exact):
    - run `python tools/check_deep_research_contract_rollout.py`
    - fail if `RESEARCH_CONTRACT_MAJOR_VERSION`/`RESEARCH_CONTRACT_MINOR_VERSION` move backwards
    - fail if major version changed and `tests/test_deep_research_contract_compat.py` was not updated in the same PR
    - run `PYTHONPATH=. pytest -q tests/test_deep_research_contract_compat.py` and require pass
  - Evidence: required `deep_research_contract_guard` job passing on `master`, linked in `docs/parallel_execution_board.md` G2 evidence.

- [ ] **Lane-sharded CI active**
  - CI execution must be split by lane/area (for example: engine, ui, integration).
  - Lane scoping must reduce unnecessary full-repo test runs and isolate failures to lane ownership.
  - Evidence: per-lane jobs visible in CI summary and selectable from PR metadata.

- [ ] **Fixture modularization complete**
  - Shared fixtures must be decomposed into lane-scoped modules so changes in one lane do not cascade unrelated failures.
  - Cross-lane fixture imports must be minimized and documented.
  - Evidence: fixture dependency map reviewed and committed.

- [ ] **PR template lane metadata mandatory**
  - PR template must require lane selection and contract-impact declaration.
  - CI must fail when lane metadata is missing or invalid.
  - Evidence: template fields + metadata validation check in required CI.

## Measurable thresholds (must hold before GO)

Track for a rolling 2-week window unless noted otherwise.

- [ ] **Cross-lane conflict rate < 10% of PRs**
  - Metric: `% of merged PRs requiring conflict resolution across >1 lane`.
  - Threshold: `< 10%`.
  - Capture method (required):
    1. Export merged PRs for pilot window (base `master`) to `artifacts/pilot/merged_prs.json`.
    2. Export conflict-resolution events to `artifacts/pilot/conflict_events.json`.
       - Count a conflict-resolution event when a PR includes one of:
         - GitHub `mergeable_state = dirty` transition before merge,
         - explicit `git merge --continue`/`rebase --continue` note in PR timeline,
         - labeled incident in `docs/pilot_incident_log_template.md` entries.
    3. Compute with `scripts/calc_conflict_rate.py`:
       `python scripts/calc_conflict_rate.py --merged-prs artifacts/pilot/merged_prs.json --conflict-events artifacts/pilot/conflict_events.json --window-start <ISO8601> --window-end <ISO8601> --output artifacts/pilot/conflict_rate_summary.json`.
    4. Store machine-readable fields: `window_start`, `window_end`, `merged_pr_count`, `conflict_resolution_events`, `conflict_rate`.
  - Evidence: summary JSON linked in `docs/parallel_pilot_evidence_index.md`.

- [ ] **Median CI duration < 8 minutes**
  - Metric: median wall-clock time for required CI checks on merged PRs.
  - Threshold: `< 8:00`.
  - Capture method: run `scripts/aggregate_ci_timings.py` against exported workflow-run JSON and archive output at `artifacts/pilot/ci_timing_summary.json`.

- [ ] **Zero unreviewed contract-breaking merges**
  - Metric: count of breaking contract/version changes merged without required contract reviewer approval.
  - Threshold: `0` over the measured window.

If any threshold fails, decision is **NO-GO** until corrective actions are completed and metrics recover.

## 1-week pilot plan (2–3 agents)

Run a controlled pilot with **2–3 simultaneous agents** before full rollout.

### Day 1: readiness validation
- Enable and verify all five required gates in a staging branch or protected test repo.
- Dry-run two intentionally malformed PRs (missing lane metadata, contract-breaking change without review) to confirm hard failures.

### Days 2–4: pilot execution
- Run normal feature/fix flow with 2–3 agents using lane assignment.
- Require daily triage for:
  - cross-lane merge conflicts,
  - CI latency regressions,
  - contract-version warnings/failures.
- Log every incident in `docs/pilot_incident_log_template.md` (date, lane, severity, root cause, resolution).

### Day 5: pilot review and decision
- Generate 1-week report with:
  - conflict rate,
  - CI median/p95,
  - contract-governance violations,
  - top failure causes and remediation actions.
- Record a formal go/no-go decision.
- Publish `docs/parallel_pilot_evidence_index.md` with links to all supporting artifacts.

## Scale-up trigger (pilot -> full multi-agent)

Proceed to full multi-agent operation only when:

1. All five required gates remain active and enforced.
2. All measurable thresholds are satisfied at pilot close.
3. No Severity-1 coordination incidents occurred during pilot week.
4. On-call/ownership rotation for lane triage is staffed and documented.

If any condition is unmet, extend pilot by 1 additional week with a focused remediation backlog.
