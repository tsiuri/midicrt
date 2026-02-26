"""Deep-research Track A/Track B split modules."""

from .logic import run_research
from .mock_module import DeterministicMockResearchModule
from .platform import (
    IPCFreshnessMeta,
    ResearchCadenceScheduler,
    ResearchContract,
    build_contract,
    freeze_payload,
    freshness_meta,
    thaw_payload,
)

__all__ = [
    "DeterministicMockResearchModule",
    "IPCFreshnessMeta",
    "ResearchCadenceScheduler",
    "ResearchContract",
    "build_contract",
    "freeze_payload",
    "freshness_meta",
    "run_research",
    "thaw_payload",
]
