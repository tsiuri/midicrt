# Parallel Pilot Daily Incident Log Template

Use one section per incident during the pilot week. Keep this file append-only during execution so reviewers can audit chronology.

## Daily header

- Date: `YYYY-MM-DD`
- Reporter: `<name>`
- Lane(s): `platform | logic | qa-contract | observer`
- Shift/Window: `<UTC time range>`

## Incident entry template

- Incident ID: `INC-YYYYMMDD-XX`
- Date/Time (UTC): `YYYY-MM-DD HH:MM`
- Lane: `<single owning lane>`
- Severity: `Sev-1 | Sev-2 | Sev-3`
- Summary: `<one-line description>`
- Root cause category: `process | tooling | test-flake | ownership-boundary | contract-version | other`
- Root cause detail: `<what actually failed>`
- Detection method: `<CI failure / review / manual test / monitoring>`
- Resolution: `<what fixed it>`
- Resolution owner: `<name>`
- Resolution completed at (UTC): `YYYY-MM-DD HH:MM`
- Preventive action: `<follow-up to avoid recurrence>`
- Linked evidence:
  - PR: `<link>`
  - Workflow run: `<link>`
  - Artifact/log: `<link or path>`

## End-of-day rollup

- Incident count by severity: `Sev-1: 0, Sev-2: 0, Sev-3: 0`
- Open incidents carried to next day: `<count + IDs>`
- Coordination notes: `<handoff, blockers, owner updates>`
