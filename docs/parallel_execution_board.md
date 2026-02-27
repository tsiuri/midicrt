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
