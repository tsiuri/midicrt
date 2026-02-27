# Parallel Pilot Evidence Index

Updated: 2026-02-27

## Operating directives (enforced)

1. Launch Wave A in parallel with three lanes only:
   - WB-001 implementation team.
   - WB-003 implementation team.
   - WB-000 maintainer sync team.
2. Run a daily 15-minute blocker triage focused on:
   - unresolved gate failures,
   - stale branch baselines,
   - cross-lane approval misses.
3. Do **not** start WB-005 until WB-001, WB-003, and WB-000 are all merged and green in CI.
4. Once WB-005 readiness check passes, start pilot Day 1 with evidence capture enabled.
5. Enforce: **no new planning docs unless tied to a failing gate**; only merge work that closes blocker checklist items.

## Wave A launch board

| Lane | Team | Scope | Status | Merge/CI gate |
|---|---|---|---|---|
| WB-001 | Implementation team | Ownership guard (`CODEOWNERS` + required review path) | In progress | Must be merged and green |
| WB-003 | Implementation team | PR metadata validator (lane + branch policy) | In progress | Must be merged and green |
| WB-000 | Maintainer sync team | Baseline sync with `origin/master` | In progress | Must be merged and green |
| WB-005 | Pilot team | Real pilot execution | Blocked | Starts only after all three lanes above are merged+green |

## Daily blocker triage (15 minutes)

- **Cadence:** once per day, fixed 15-minute window.
- **Required agenda:**
  1. Gate failures unresolved since previous day.
  2. Branch baseline drift/staleness.
  3. Cross-lane approval misses.
- **Exit criteria:** every open blocker has a clear owner and next action due within 24 hours.

## WB-005 readiness gate and Day 1 trigger

Readiness is **PASS** only when all checks are true:

- [ ] WB-001 merged.
- [ ] WB-003 merged.
- [ ] WB-000 merged.
- [ ] WB-001 CI status green.
- [ ] WB-003 CI status green.
- [ ] WB-000 CI status green.

When all checks pass:

- [ ] Mark WB-005 status as `ready`.
- [ ] Start pilot Day 1.
- [ ] Enable evidence capture and refresh artifacts:
  - `artifacts/pilot/merged_prs.json`
  - `artifacts/pilot/conflict_events.json`
  - `artifacts/pilot/conflict_rate_summary.json`
  - `artifacts/pilot/ci_runs.json`
  - `artifacts/pilot/ci_timing_summary.json`

## Daily burn-down summary (one page)

Create one entry per day using the template below.

### YYYY-MM-DD

**Blocker status**
- Gate failures: `<open/closed + key IDs>`
- Baseline freshness: `<fresh/stale + branch SHA context>`
- Cross-lane approvals: `<met/missed + impacted PRs>`

**Newly closed items**
- `<checklist item ID>` — `<what closed today, PR/CI evidence>`
- `<checklist item ID>` — `<what closed today, PR/CI evidence>`

**Remaining risks**
- `<risk>` — owner: `<name>` — mitigation by `<date>`
- `<risk>` — owner: `<name>` — mitigation by `<date>`

## Evidence links

| Evidence item | Path | Status |
|---|---|---|
| Daily incident log | `docs/pilot_incident_log_template.md` | pending |
| Merged PR export | `artifacts/pilot/merged_prs.json` | pending |
| Conflict-event export | `artifacts/pilot/conflict_events.json` | pending |
| Conflict-rate summary | `artifacts/pilot/conflict_rate_summary.json` | pending |
| CI run export | `artifacts/pilot/ci_runs.json` | pending |
| CI timing summary | `artifacts/pilot/ci_timing_summary.json` | pending |
