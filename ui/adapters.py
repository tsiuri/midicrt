"""Page adapters for legacy draw(state) pages.

These helpers convert draw_line-based page implementations into widget trees
without changing page logic.
"""

from __future__ import annotations

from typing import Callable

from ui.model import Column, Line, NotesWidget, TextBlock, Widget


class _LineCapture:
    def __init__(self, cols: int, rows: int):
        self.cols = max(1, int(cols))
        self.rows = max(1, int(rows))
        self.lines = ["" for _ in range(self.rows)]

    def draw_line(self, y: int, text: str) -> None:
        if 0 <= int(y) < self.rows:
            self.lines[int(y)] = str(text)[: self.cols]


def build_widget_from_legacy_draw(
    draw_fn: Callable[[dict], None],
    state: dict,
    draw_line_ref: Callable[[int, str], None],
) -> Widget:
    """Run legacy draw() into a captured line buffer and return a TextBlock."""
    try:
        content = capture_legacy_lines(draw_fn, state, draw_line_ref)
    except Exception as exc:
        return Column(children=[TextBlock(lines=[Line.plain(f"[adapter error] {exc}")])])
    return TextBlock(lines=[Line.plain(t) for t in content])


def capture_legacy_lines(
    draw_fn: Callable[[dict], None],
    state: dict,
    draw_line_ref: Callable[[int, str], None],
) -> list[str]:
    cols = int(state.get("cols", 100))
    rows = int(state.get("rows", 30))
    cap = _LineCapture(cols=cols, rows=rows)
    module_globals = draw_fn.__globals__
    old = module_globals.get("draw_line", draw_line_ref)
    module_globals["draw_line"] = cap.draw_line
    try:
        draw_fn(state)
    finally:
        module_globals["draw_line"] = old
    y0 = int(state.get("y_offset", 0))
    return cap.lines[y0:]
