# TASK-2026-02-27-02 — Repair lane workflow graph so lane-sharded CI is actually enforceable

## Owner lane
platform

## Type
implement missing gate

## Problem
`docs/parallel_pilot_report_2026-02-26.md` marks lane-sharded CI as active, but `.github/workflows/test-lanes.yml` has workflow-graph defects:
- `track_a_tests` is declared without runner/steps.
- `ci_lane_summary.needs` references `track_b_tests` and `integration_observer_tests`, which are not defined jobs.
- `deep_research_contract_guard` condition checks `detect_changes.outputs.track_a`/`track_b`, but those outputs are not exported.

## Required changes
- Make lane jobs coherent (`platform`, `logic`, `qa-contract`, `observer`) and runnable.
- Fix `needs` to reference real job IDs.
- Fix `if` conditions to use exported outputs (or export required outputs).

## Acceptance criteria
- Workflow YAML validates with no undefined `needs` references.
- PR runs show lane jobs and summary job executing on expected change sets.
- Docs can truthfully claim lane-sharded CI is active.
