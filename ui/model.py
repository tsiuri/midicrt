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
class Spacer(Widget):
    """Fixed number of blank rows."""

    rows: int = 1


@dataclass(frozen=True)
class Frame:
    """Target text frame dimensions."""

    cols: int
    rows: int
