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
| Merged PR export | `artifacts/pilot/merged_prs.json` | pending | Source for merged PR denominator in conflict metric. |
| Conflict-event export | `artifacts/pilot/conflict_events.json` | pending | Raw conflict-resolution events observed during window. |
| Conflict-rate summary | `artifacts/pilot/conflict_rate_summary.json` | pending | Machine-readable numerator/denominator + computed rate. |
| CI run export | `artifacts/pilot/ci_runs.json` | pending | Raw workflow run data for timing analysis. |
| CI timing summary | `artifacts/pilot/ci_timing_summary.json` | pending | Produced by `scripts/aggregate_ci_timings.py` (median/p95). |
| Contract-governance violations tally | `<link>` | pending | Count and evidence of any unreviewed breaking merges. |
| Final pilot report | `docs/parallel_pilot_report_YYYY-MM-DD.md` | pending | Narrative summary and remediation items. |
| Formal GO/NO-GO decision record | `<link>` | pending | Final decision artifact with approver sign-off. |

## Metric extraction commands

Run these commands and attach resulting artifacts to this index.

```bash
# 1) Produce CI timing summary (median + p95) from exported workflow run JSON.
python scripts/aggregate_ci_timings.py \
  --input artifacts/pilot/ci_runs.json \
  --output artifacts/pilot/ci_timing_summary.json

# 2) Produce conflict-rate summary from merged PR and conflict-event exports.
python scripts/calc_conflict_rate.py \
  --merged-prs artifacts/pilot/merged_prs.json \
  --conflict-events artifacts/pilot/conflict_events.json \
  --window-start 2026-03-04T00:00:00Z \
  --window-end 2026-03-08T23:59:59Z \
  --output artifacts/pilot/conflict_rate_summary.json

# 3) (Reference) Conflict rate formula used in summary artifact.
# conflict_rate = conflict_resolution_events / merged_pr_count
```

## Sign-off checklist

- [ ] All required evidence links populated.
- [ ] Conflict rate threshold evaluated (`< 10%`).
- [ ] CI median threshold evaluated (`< 8 minutes`), p95 recorded.
- [ ] Contract-governance violations evaluated (`must be 0`).
- [ ] Severity-1 coordination incidents evaluated (`must be 0`).
- [ ] Final GO/NO-GO decision published.
