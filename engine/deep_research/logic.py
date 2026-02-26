from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .platform import ResearchContract, contract_versions_compatible, current_contract_version


@dataclass(frozen=True)
class ResearchResult:
    motif_span: int
    note_density: str
    active_note_total: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "motif_span": self.motif_span,
            "note_density": self.note_density,
            "active_note_total": self.active_note_total,
        }


def _contract_version_error(expected_version: str, actual_version: str) -> dict[str, Any]:
    return {
        "status": "error",
        "error": {
            "code": "deep_research_contract_incompatible",
            "expected_contract_version": expected_version,
            "actual_contract_version": actual_version,
            "message": "Deep research contract version is incompatible; staged rollout required.",
        },
    }


def run_research(contract: ResearchContract) -> dict[str, Any]:
    """Track B logic: consumes only frozen contract input."""
    expected_version = current_contract_version()
    if not contract_versions_compatible(expected_version, contract.contract_version):
        return _contract_version_error(expected_version, contract.contract_version)

    transport = dict(contract.transport)
    active_notes = dict(contract.active_notes)
    flattened = [note for notes in active_notes.values() for note in notes]
    active_total = len(flattened)

    if active_total <= 1:
        density = "sparse"
    elif active_total <= 4:
        density = "medium"
    else:
        density = "dense"

    tick = int(transport.get("tick", 0))
    motif_span = 0 if not flattened else (max(flattened) - min(flattened) + (tick % 3))

    return ResearchResult(
        motif_span=motif_span,
        note_density=density,
        active_note_total=active_total,
    ).as_dict()
