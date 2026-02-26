from __future__ import annotations

from dataclasses import asdict, dataclass

from .platform import ResearchContract


@dataclass(frozen=True)
class MockResearchOutput:
    signature: str
    active_channel_count: int
    bpm: float


class DeterministicMockResearchModule:
    """Track A placeholder with deterministic, testable output."""

    name = "deepresearch"

    @staticmethod
    def run(contract: ResearchContract) -> dict[str, object]:
        active = dict(contract.active_notes)
        transport = dict(contract.transport)
        output = MockResearchOutput(
            signature=f"v{contract.schema_version}:{contract.event_kind}:{transport.get('tick', 0)}",
            active_channel_count=len(active),
            bpm=float(transport.get("bpm", 0.0)),
        )
        return asdict(output)
