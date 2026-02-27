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


## PR metadata guard operator notes

The `pr-metadata-guard` required check (workflow: `.github/workflows/pr-metadata-guard.yml`) enforces branch naming, lane declaration, and cross-lane escalation metadata.

### Common failure modes

- Branch name does not match `agent/<lane>/<ticket>-<slug>`.
- PR body is missing `Lane:` or does not use one of `platform|logic|qa-contract|observer`.
- PR body omits the `contract-impact` declaration block from the PR template.
- Changed files cross lane ownership boundaries without the `contract-impact` checkbox marked as checked.

### Override / recovery process

- There is **no bypass label** for PR metadata guard; fix the branch name and/or PR body metadata and push again.
- For legitimate cross-lane changes, mark the `contract-impact` checkbox and populate all required fields:
  - `impacted lane(s)` must be non-empty.
  - `required downstream handoff artifacts published` must include at least one URL/Markdown link.
- Lane/path conflicts are only allowed when `contract-impact` is checked (`yes/true` by checkbox), and they must include the handoff artifact links above.
- If branch naming is wrong, rename locally and push the corrected branch, then update the PR source branch.

### Metadata guard failure messages and remediations

- `Branch name must match agent/<lane>/<ticket>-<slug> ...`
  - Rename the branch to match playbook format and keep lane in `platform|logic|qa-contract|observer`.
- `PR template must set '- Lane:' ...`
  - Set the lane explicitly in the PR body.
- `PR template must include the contract-impact declaration field.`
  - Restore the contract-impact block from `.github/pull_request_template.md`.
- `Lane ownership conflict ... Set contract-impact to true and provide handoff artifacts.`
  - Either keep changes within the declared lane paths, or check contract-impact and add impacted lanes + links.
- `Cross-lane edits detected without contract-impact marker ...`
  - Check contract-impact and include the downstream artifact links.
- `contract-impact is checked, but ... missing or empty.`
  - Fill missing `impacted lane(s)` and/or `required downstream handoff artifacts published` values.

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

## Contract governance policy details (WB-002)

The `contract-version-governance` required check (workflow: `.github/workflows/contract-version-governance.yml`) enforces explicit path-scoped contract governance on pull requests.

### Governed contract surfaces (explicit glob map)

- `lane-interface-docs`
  - `docs/parallel_readiness_checklist.md`
  - `docs/parallel_execution_board.md`
  - `engine/deep_research/contract_versioning.md`
- `shared-schema-config`
  - `engine/deep_research/platform.py`
  - `engine/deep_research/logic.py`
  - `engine/state/schema.py`
  - `config/settings.json`
- `fixture-contracts`
  - `tests/fixtures/**/*.json`
  - `tests/fixtures/**/*.yaml`
  - `tests/test_deep_research_contract_compat.py`

### Required PR declarations when governed paths are touched

1. `Contract-Impact: none|additive|breaking` must exist in the PR body.
2. One of the following must exist:
   - a contract version bump in `engine/deep_research/platform.py` (`RESEARCH_CONTRACT_MAJOR_VERSION` / `RESEARCH_CONTRACT_MINOR_VERSION`), or
   - `Contract-Version-Exception: <structured reason + rollout/test plan>` in PR body.
3. If `Contract-Impact: breaking`:
   - label `contract-breaking-approved` is required, and
   - at least one approval must come from a designated reviewer listed in the workflow.

### Actionable failures in CI

The check prints exactly:
- each changed file that matched a contract surface glob,
- the surface/rule category that was triggered,
- each failed requirement as a numbered remediation item.

### PR body examples

**Additive change with version bump**

```md
Contract-Impact: additive
Contract-Version-Exception: n/a
```

**Breaking change with exception note (while reader-first rollout is in flight)**

```md
Contract-Impact: breaking
Contract-Version-Exception: ADR-0123 phase 1/4 (reader-first):
- no writer flip in this PR
- compat tests updated in tests/test_deep_research_contract_compat.py
- rollout validation planned in staging before major increment
```

**Docs-only touch on a governed file without version bump**

```md
Contract-Impact: none
Contract-Version-Exception: docs-only update to governance wording; no payload/schema change
```

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
