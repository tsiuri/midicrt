"""Renderer protocol for widget trees."""

from typing import Protocol, List

from ui.model import Frame, Widget


class Renderer(Protocol):
    """Render a widget tree into terminal-safe lines."""

    def render(self, widget: Widget, frame: Frame) -> List[str]:
        ...
