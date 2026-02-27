"""Widget tree and layout primitives for TTY-safe rendering."""

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class Style:
    """Minimal style hints for renderers.

    Keep this palette intentionally small for monochrome CRT compatibility.
    """

    reverse: bool = False
    bold: bool = False


@dataclass(frozen=True)
class Segment:
    """Text segment rendered within a line."""

    text: str
    style: Style = field(default_factory=Style)


@dataclass(frozen=True)
class Line:
    """A single row of text output, as ordered segments."""

    segments: List[Segment] = field(default_factory=list)

    @staticmethod
    def plain(text: str) -> "Line":
        return Line([Segment(text=text)])


@dataclass(frozen=True)
class Widget:
    """Base widget node."""


@dataclass(frozen=True)
class Column(Widget):
    """Vertical stack of child widgets."""

    children: List[Widget] = field(default_factory=list)


@dataclass(frozen=True)
class TextBlock(Widget):
    """Simple block of precomputed lines."""

    lines: List[Line] = field(default_factory=list)


@dataclass(frozen=True)
class PageLinesWidget(Widget):
    """Generic structured page payload for non-captured page adapters."""

    page_id: int
    page_name: str
    lines: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class Spacer(Widget):
    """Fixed number of blank rows."""

    rows: int = 1


@dataclass(frozen=True)
class Frame:
    """Target text frame dimensions."""

    cols: int
    rows: int


@dataclass(frozen=True)
class PianoRollCell:
    """Single piano-roll cell state for a pitch/time slot."""

    velocity: int = 0
    channel: int | None = None


@dataclass(frozen=True)
class PianoRollWidget(Widget):
    """Grid representation for piano-roll pages.

    Rows are ordered high->low pitch, columns left->right time.
    """

    pitches: List[int] = field(default_factory=list)
    cells: List[List[PianoRollCell]] = field(default_factory=list)
    columns: List[List[tuple[int, int, int]]] = field(default_factory=list)
    spans: List[tuple[int, int, int, int, int]] = field(default_factory=list)
    pitch_low: int = 0
    pitch_high: int = 0
    ticks_per_col: int = 1
    tick_right: int = 0
    tick_now: int = 0
    timeline: str = ""
    left_margin: int = 10
    style_mode: str = "text"


@dataclass(frozen=True)
class TransportWidget(Widget):
    """Stable transport payload shared by all renderers."""

    running: bool = False
    bpm: float = 0.0
    bar: int = 0
    tick: int = 0
    time_signature: str = ""


@dataclass(frozen=True)
class NotesWidget(Widget):
    """Renderer-facing notes-page payload (line-semantics contract)."""

    lines: List[Line] = field(default_factory=list)


@dataclass(frozen=True)
class EventLogWidget(Widget):
    """Structured event log payload shared across renderers."""

    title: str = ""
    filter_summary: str = ""
    entries: List[str] = field(default_factory=list)
    marker: str = ""


@dataclass(frozen=True)
class FooterStatusWidget(Widget):
    """Footer/status line payload."""

    left: str = ""
    right: str = ""


@dataclass(frozen=True)
class TempoQualityWidget(Widget):
    """Tempo-estimation quality panel payload."""

    bpm: float = 0.0
    confidence: float = 0.0
    stability: float = 0.0
    lock_state: str = "unlocked"
    meter: str = ""


@dataclass(frozen=True)
class MicrotimingHistogramWidget(Widget):
    """Microtiming offsets grouped into signed bins."""

    title: str = "Microtiming"
    buckets: List[tuple[str, int]] = field(default_factory=list)
    total_samples: int = 0


@dataclass(frozen=True)
class CaptureStatusWidget(Widget):
    """Capture/export status plus last commit metadata."""

    armed: bool = False
    state: str = "idle"
    target_path: str = ""
    last_commit: str = ""
    last_commit_age_s: float | None = None


@dataclass(frozen=True)
class ModuleHealthCard:
    """One module health summary card."""

    name: str
    status: str = "ok"
    latency_ms: float = 0.0
    drop_rate: float = 0.0
    detail: str = ""


@dataclass(frozen=True)
class ModuleHealthWidget(Widget):
    """Collection of module health cards."""

    cards: List[ModuleHealthCard] = field(default_factory=list)
