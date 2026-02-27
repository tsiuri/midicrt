from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class PageViewContract:
    """Contract envelope for line-oriented page views produced by engine code."""

    contract: str
    version: int
    page_id: int
    cols: int
    rows: int
    y_offset: int
    lines: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "version": int(self.version),
            "page_id": int(self.page_id),
            "cols": int(self.cols),
            "rows": int(self.rows),
            "y_offset": int(self.y_offset),
            "lines": list(self.lines),
        }


class _LineCapture:
    def __init__(self, cols: int, rows: int):
        self.cols = max(1, int(cols))
        self.rows = max(1, int(rows))
        self.lines = ["" for _ in range(self.rows)]

    def draw_line(self, y: int, text: str) -> None:
        y_int = int(y)
        if 0 <= y_int < self.rows:
            self.lines[y_int] = str(text)[: self.cols]


def _normalize_state(state: dict[str, Any]) -> tuple[int, int, int, int]:
    cols = int(state.get("cols", 100))
    rows = int(state.get("rows", 30))
    y_offset = int(state.get("y_offset", 0))
    page_id = int(state.get("current_page", 0))
    return cols, rows, y_offset, page_id


def build_legacy_page_view_contract(
    draw_fn: Callable[[dict[str, Any]], None],
    state: dict[str, Any],
    draw_line_ref: Callable[[int, str], None],
) -> PageViewContract:
    """Capture a legacy draw(state) page into an explicit contract payload."""

    cols, rows, y_offset, page_id = _normalize_state(state)
    cap = _LineCapture(cols=cols, rows=rows)
    module_globals = draw_fn.__globals__
    old = module_globals.get("draw_line", draw_line_ref)
    module_globals["draw_line"] = cap.draw_line
    try:
        draw_fn(state)
    finally:
        module_globals["draw_line"] = old

    return PageViewContract(
        contract="legacy.page.view",
        version=1,
        page_id=page_id,
        cols=cols,
        rows=rows,
        y_offset=y_offset,
        lines=tuple(cap.lines[y_offset:]),
    )
