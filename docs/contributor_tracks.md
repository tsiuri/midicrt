# Deep Research Ownership Tracks

- **Track A (platform):** `engine/deep_research/platform.py`, `engine/deep_research/mock_module.py`
  - Owns contract assembly, cadence scheduling, frozen snapshot handoff helpers, and IPC freshness metadata.
  - **Do not edit Track B files** when making platform-only changes.

- **Track B (research logic):** `engine/deep_research/logic.py`, `tests/fixtures/deep_research_sequences/*.json`, `tests/test_deep_research_tracks.py`
  - Owns algorithm behavior and deterministic expected outputs from fixture-driven MIDI/event sequences.
  - Consumes only `ResearchContract` input.
  - **Do not edit Track A files** for logic-only changes unless the contract itself changes.

## Cross-track override policy

CI rejects pull requests that modify both Track A and Track B files unless an explicit override is set.

Allowed overrides (contract-only changes):
- Set `ALLOW_CROSS_TRACK=1` (for local/one-off CI runs).
- Apply PR label `allow-cross-track` (workflow maps this label to `ALLOW_CROSS_TRACK=1`).
- Add `.ci/allow_cross_track` in the branch to provide a file-based override.

Only use an override when the contract boundary itself changes and both tracks must be updated together.

## Parallel multi-agent workflow

For lane definitions, branch naming, PR size limits, and the dependent-work handoff protocol, see:

- `docs/parallel_dev_playbook.md`

## Start-from-here baseline (2026-02-27)

> Use this block before starting any lane work.

1. Sync refs (maintainer environment): `git fetch origin master --prune`
2. Branch from the shared baseline SHA: `6218588ec031ce6993dea01597db5d9ec22a1531`
3. Create lane branch from baseline: `git checkout -b <lane>/<ticket> 6218588ec031ce6993dea01597db5d9ec22a1531`
4. Verify baseline anchor: `git rev-parse --short HEAD`
5. Record lane start in PR description: baseline SHA + UTC start timestamp.

This SHA is canonical for this sprint window. All sprint branches must start from this exact anchor; stale-base PRs are rejected by CI via `.github/workflows/baseline-anchor-guard.yml`.

