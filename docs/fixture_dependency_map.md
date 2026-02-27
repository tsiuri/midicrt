# Fixture Dependency Map

This artifact documents fixture providers and consumers by lane/path so fixture updates remain scoped and contract-safe.

## Update contract

- If any fixture-related provider/consumer path in this document changes, update this file in the same pull request.
- If a map update is intentionally deferred, add a documented exception entry to `.ci/fixture_map_exceptions.txt` in the same pull request.

## Lane-scoped dependency map

| Lane | Role | Path | Contract-sensitive | Notes |
|---|---|---|---|---|
| Platform / QA-Contract | Provider | `tests/fixtures/schema_normalization_cases.json` | Yes | Schema normalization regression vectors for contract-safe payload shaping. |
| Platform / QA-Contract | Provider | `tests/fixtures/tempo_map_replay.json` | No | Deterministic tempo timeline replay cases. |
| Platform / QA-Contract | Provider | `tests/fixtures/capture_replay.json` | Yes | Capture replay baseline shared by replay + contract assertions. |
| Logic / QA-Contract | Provider | `tests/fixtures/deep_research_contract_cases.json` | Yes | Deep-research IPC/schema compatibility and stale-result policy fixtures. |
| Logic (Track B) | Provider | `tests/fixtures/deep_research_sequences/*.json` | Yes | Lane-owned deep-research sequence scenarios loaded in filename order. |
| Logic / QA-Contract | Consumer | `tests/deep_research_fixture_loader.py` | Yes | Enforces deep-research fixture schema, required keys, and unique `name`. |
| Platform | Consumer | `tests/test_schema_contract.py` | Yes | Consumes schema-normalization fixture cases. |
| Platform | Consumer | `tests/test_tempo_map_metrics.py` | No | Consumes tempo replay fixture. |
| Platform | Consumer | `tests/test_capture_export.py` | No | Consumes capture replay fixture for deterministic export checks. |
| Platform + Logic | Consumer | `tests/test_engine_replay_determinism.py` | Yes | Uses capture replay fixture for deterministic replay/state assertions. |
| QA-Contract | Consumer | `tests/test_deep_research_replay_contracts.py` | Yes | Uses capture replay + deep-research contract fixtures for compatibility checks. |
| Logic (Track B) | Consumer | `tests/test_deep_research_tracks.py` | Yes | Uses loader + deep-research sequence fixtures for lane contract assertions. |

## CI guard scope (drift detection)

The CI workflow `.github/workflows/fixture-dependency-map-guard.yml` monitors changes to these fixture-related paths:

- `tests/fixtures/**/*.json`
- `tests/deep_research_fixture_loader.py`
- `tests/test_schema_contract.py`
- `tests/test_tempo_map_metrics.py`
- `tests/test_capture_export.py`
- `tests/test_engine_replay_determinism.py`
- `tests/test_deep_research_replay_contracts.py`
- `tests/test_deep_research_tracks.py`

When one or more monitored paths are changed, CI requires either:

1. `docs/fixture_dependency_map.md` changed in the same diff, **or**
2. `.ci/fixture_map_exceptions.txt` changed with an explicit exception entry.

## Deep-research sequence ownership guidance

- Add or modify only the scenario file you own under `tests/fixtures/deep_research_sequences/`.
- Keep `name` unique per fixture file; duplicate names fail loader validation.
- Keep lane/topic filename prefixes (`logic_density_*`, `transport_tick_*`) so ownership stays clear.
