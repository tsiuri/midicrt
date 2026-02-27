# Fixture Dependency Map

This map documents how deep-research fixture files are consumed so fixture updates can stay lane-scoped and conflict-free.

## Source fixtures (directory-scoped)

- `tests/fixtures/deep_research_sequences/logic_density_single_note_sparse.json`
- `tests/fixtures/deep_research_sequences/logic_density_triad_medium_density.json`
- `tests/fixtures/deep_research_sequences/transport_tick_stacked_dense.json`

## Loader and validation dependency chain

1. `tests/deep_research_fixture_loader.py`
   - discovers fixture files from `tests/fixtures/deep_research_sequences/*.json`
   - loads files in deterministic sorted filename order
   - validates fixture schema per file
   - rejects duplicate fixture `name` values across files
2. `tests/test_deep_research_tracks.py`
   - consumes `load_all_deep_research_sequence_fixtures()` for replay assertions
   - verifies filename lane/topic naming conventions
   - verifies duplicate-name protection

## Parallel edit guidance

- Add or modify only the scenario file you own under `tests/fixtures/deep_research_sequences/`.
- Avoid reshaping unrelated scenarios to minimize merge overlap.
- Keep `name` unique per fixture file; duplicate names fail validation.
- Use lane/topic filename prefixes (`logic_density_*`, `transport_tick_*`) to preserve ownership boundaries.
