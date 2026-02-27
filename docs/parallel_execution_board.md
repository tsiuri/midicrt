# Parallel Execution Board

Track execution gates and objective evidence in this board.

| Gate | Status | Owner | Evidence (required) |
|---|---|---|---|
| G1: Ownership boundaries enforced | In Progress | Platform | `Track Boundaries` workflow run + blocked PR screenshot/log |
| G2: DeepResearch contract rollout guard enforced | In Progress | Platform + Logic | `Test Lanes / deep_research_contract_guard` run URL and `tests/test_deep_research_contract_compat.py` output summary |
| G3: Lane-sharded CI healthy | In Progress | QA | `ci_lane_summary.md` artifact from `Test Lanes` |

## G2 evidence field (required)

When posting evidence for G2, include all of the following:

- workflow run URL for `Test Lanes`
- pass/fail output for `Validate contract rollout declarations` (`python tools/check_deep_research_contract_rollout.py`)
- pass/fail output for compatibility tests (`PYTHONPATH=. pytest -q tests/test_deep_research_contract_compat.py`)
- explicit note if a major contract version change occurred in the PR and whether `tests/test_deep_research_contract_compat.py` was updated in that same PR
