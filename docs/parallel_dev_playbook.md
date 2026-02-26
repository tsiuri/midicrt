# Parallel Development Playbook

This playbook defines an explicit multi-agent workflow so concurrent contributors can work in parallel without conflicting changes.

## Lane types

### `platform`
- Scope: runtime/platform infrastructure, orchestration, I/O boundaries, renderer/framework plumbing, and shared contracts.
- Typical directories: `engine/`, `fb/`, `ui/`, `scripts/`, `.github/workflows/`.
- Must publish contract notes whenever interfaces consumed by other lanes change.

### `logic`
- Scope: feature behavior, page/plugin logic, deterministic rule changes, and fixture-driven behavior changes.
- Typical directories: `pages/`, `plugins/`, `engine/deep_research/logic.py`, `tests/fixtures/`.
- Must avoid platform/infrastructure rewrites unless escalated via contract-impact workflow.

### `qa-contract`
- Scope: tests, fixtures, acceptance checks, track-boundary assertions, and contract validation.
- Typical directories: `tests/`, `.ci/`, `docs/` (test protocol docs).
- Must document evidence for all changed contracts and produce reproducible commands.

### `observer`
- Scope: read-only dashboards, observer APIs, metrics surfaces, and operator-facing telemetry docs.
- Typical directories: `web/`, `scripts/run_web_observer.py`, observer-specific docs.
- Must preserve read-only guarantees and publish security assumptions for any endpoint changes.

## Required branch naming

All branches must follow:

- `agent/<lane>/<ticket>-<slug>`

Examples:
- `agent/platform/1234-contract-refresh`
- `agent/logic/1288-voice-monitor-thresholds`
- `agent/qa-contract/1301-fixture-hardening`
- `agent/observer/1310-websocket-metrics`

Rules:
- `<lane>` must be one of: `platform`, `logic`, `qa-contract`, `observer`.
- `<ticket>` should be a stable work item ID (`1234`, `MIDI-1234`, etc.).
- `<slug>` should be short, lowercase, and hyphenated.

## PR limits by lane

Keep changes small and isolated. If limits are exceeded, split into dependent PRs and use the handoff protocol.

| Lane | Soft max PR size | Hard max touched directories* |
|---|---:|---:|
| platform | 500 lines changed | 5 |
| logic | 350 lines changed | 4 |
| qa-contract | 450 lines changed | 5 |
| observer | 300 lines changed | 3 |

\* Count top-level ownership directories (example: `engine/`, `pages/`, `tests/`, `web/`, `docs/`).

If a PR exceeds a hard limit:
1. Mark as `contract-impact` in the PR template.
2. Explain why splitting is unsafe.
3. Include explicit cross-lane reviewer sign-off.

## Handoff protocol (for dependent work)

No downstream lane should begin implementation until upstream handoff artifacts are published in the upstream PR description (or linked doc).

### Platform → logic / qa-contract handoff
Publish:
- Contract diff summary (inputs/outputs, versioning notes).
- Migration notes with before/after examples.
- Minimal fixture seed or mock payload for downstream testing.
- Rollback guidance.

### Logic → qa-contract handoff
Publish:
- Behavior matrix (expected outcomes per scenario).
- New/updated fixtures with deterministic IDs.
- Edge cases explicitly out of scope.
- Any temporary flags used for rollout.

### Observer → qa-contract handoff
Publish:
- Endpoint/event schema changes and sample payloads.
- Read-only/security invariants checklist.
- Throughput/rate-limit expectations for test planning.

### qa-contract → all lanes handback
Publish:
- Command transcript for validation.
- Pass/fail matrix mapped to contract points.
- Known gaps or flaky areas with owner assignment.

## Ownership and escalation

- A lane may not modify another lane’s owned files unless `contract-impact` is checked and justified.
- Contract-impact changes require at least one reviewer from each affected lane.
- Prefer sequential handoffs over large cross-lane PRs.
