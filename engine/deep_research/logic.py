from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .platform import ResearchContract


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


def run_research(contract: ResearchContract) -> dict[str, Any]:
    """Track B logic: consumes only frozen contract input."""
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
