# Deep Research Ownership Tracks

- **Track A (platform):** `engine/deep_research/platform.py`, `engine/deep_research/mock_module.py`
  - Owns contract assembly, cadence scheduling, frozen snapshot handoff helpers, and IPC freshness metadata.
  - **Do not edit Track B files** when making platform-only changes.

- **Track B (research logic):** `engine/deep_research/logic.py`, `tests/fixtures/deep_research_sequences.json`, `tests/test_deep_research_tracks.py`
  - Owns algorithm behavior and deterministic expected outputs from fixture-driven MIDI/event sequences.
  - Consumes only `ResearchContract` input.
  - **Do not edit Track A files** for logic-only changes unless the contract itself changes.
