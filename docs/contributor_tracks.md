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
