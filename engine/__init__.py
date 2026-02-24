"""midicrt engine package."""

try:
    from .core import EngineState, MidiEngine
except Exception:  # optional in lightweight/test imports
    EngineState = None
    MidiEngine = None

from .ipc import SnapshotPublisher

__all__ = ["EngineState", "MidiEngine", "SnapshotPublisher"]
