# Parallel Pilot Evidence Index

Use this index as the single source of links for pilot GO/NO-GO review.
Update links daily during WB-005 and mark each entry as `pending` or `complete`.

## Pilot metadata

- Pilot window start (UTC): `YYYY-MM-DD`
- Pilot window end (UTC): `YYYY-MM-DD`
- Decision date (UTC): `YYYY-MM-DD`
- Decision owner: `<name>`

## Required evidence links

| Evidence item | Path / Link | Status | Notes |
|---|---|---|---|
| Daily incident log | `docs/pilot_incident_log_template.md` | pending | Must include date, lane, severity, root cause, resolution for each incident. |
| Merged PR export | [`artifacts/pilot/merged_prs.json`](../artifacts/pilot/merged_prs.json) | pending | Source for merged PR denominator in conflict metric. |
| Conflict-event export | [`artifacts/pilot/conflict_events.json`](../artifacts/pilot/conflict_events.json) | pending | Raw conflict-resolution events observed during window. |
| Conflict-rate summary | [`artifacts/pilot/conflict_rate_summary.json`](../artifacts/pilot/conflict_rate_summary.json) | pending | Machine-readable numerator/denominator + computed rate. |
| CI run export | [`artifacts/pilot/ci_runs.json`](../artifacts/pilot/ci_runs.json) | pending | Raw workflow run data for timing analysis. |
| CI timing summary | [`artifacts/pilot/ci_timing_summary.json`](../artifacts/pilot/ci_timing_summary.json) | pending | Produced by daily orchestrator (median/p95). |
| Contract-governance violations tally | `<link>` | pending | Count and evidence of any unreviewed breaking merges. |
| Final pilot report | `docs/parallel_pilot_report_YYYY-MM-DD.md` | pending | Narrative summary and remediation items. |
| Formal GO/NO-GO decision record | `<link>` | pending | Final decision artifact with approver sign-off. |


## Governance gates

| Gate evidence | Path / Link | Status | Notes |
|---|---|---|---|
| Branch protection config (master) | [`.github/branch-protection/master.json`](../.github/branch-protection/master.json) | complete | Requires CODEOWNER review on protected default branch (`require_code_owner_reviews: true`). |
| Dry-run PR: single-lane approval passes | [`artifacts/pilot/governance/dry_run_single_lane_pass.json`](../artifacts/pilot/governance/dry_run_single_lane_pass.json) | complete | Simulates lane-local PR touching only logic roots with logic approval. |
| Dry-run PR: cross-lane blocked without all lane approvals | [`artifacts/pilot/governance/dry_run_cross_lane_blocked.json`](../artifacts/pilot/governance/dry_run_cross_lane_blocked.json) | complete | Simulates platform+logic PR with only platform approval (fails as expected). |
| Dry-run PR: cross-lane unblocks after all touched lane approvals | [`artifacts/pilot/governance/dry_run_cross_lane_after_both_approve.json`](../artifacts/pilot/governance/dry_run_cross_lane_after_both_approve.json) | complete | Simulates same cross-lane PR after platform+logic approvals (passes). |

## Daily execution checklist (WB-005)

- [ ] Day 1 (`YYYY-MM-DD`) — exports refreshed, orchestrator run, links verified.
- [ ] Day 2 (`YYYY-MM-DD`) — exports refreshed, orchestrator run, links verified.
- [ ] Day 3 (`YYYY-MM-DD`) — exports refreshed, orchestrator run, links verified.
- [ ] Day 4 (`YYYY-MM-DD`) — exports refreshed, orchestrator run, links verified.
- [ ] Day 5 (`YYYY-MM-DD`) — exports refreshed, orchestrator run, links verified.

## Metric extraction commands

Run these commands and attach resulting artifacts to this index.

```bash
# 0) One-time setup: create missing pilot artifacts with deterministic skeletons.
bash artifacts/pilot/bootstrap.sh

# 1) Daily aggregation orchestrator (stable output paths under artifacts/pilot/).
python scripts/run_parallel_pilot_daily.py \
  --window-start 2026-03-04T00:00:00Z \
  --window-end 2026-03-08T23:59:59Z

# 2) Reference commands run by the orchestrator.
python scripts/calc_conflict_rate.py \
  --merged-prs artifacts/pilot/merged_prs.json \
  --conflict-events artifacts/pilot/conflict_events.json \
  --window-start 2026-03-04T00:00:00Z \
  --window-end 2026-03-08T23:59:59Z \
  --output artifacts/pilot/conflict_rate_summary.json

python scripts/aggregate_ci_timings.py \
  --input artifacts/pilot/ci_runs.json \
  --output artifacts/pilot/ci_timing_summary.json

# 3) Conflict rate formula used in summary artifact.
# conflict_rate = conflict_resolution_events / merged_pr_count
```

## Sign-off checklist

- [ ] All required evidence links populated.
- [ ] Conflict rate threshold evaluated (`< 10%`).
- [ ] CI median threshold evaluated (`< 8 minutes`), p95 recorded.
- [ ] Contract-governance violations evaluated (`must be 0`).
- [ ] Severity-1 coordination incidents evaluated (`must be 0`).
- [ ] Final GO/NO-GO decision published.
