from __future__ import annotations

from typing import Any

from ui.model import Column, Line, TextBlock, Widget


LEGACY_PAGE_VIEW_CONTRACT = "legacy.page.view"


def page_view_lines(payload: dict[str, Any]) -> list[str]:
    if str(payload.get("contract", "")) != LEGACY_PAGE_VIEW_CONTRACT:
        return []
    lines = payload.get("lines", [])
    if not isinstance(lines, list):
        return []
    return [str(line) for line in lines]


def widget_from_page_view(payload: dict[str, Any]) -> Widget:
    contract = str(payload.get("contract", ""))
    if contract != LEGACY_PAGE_VIEW_CONTRACT:
        return Column(children=[TextBlock(lines=[Line.plain(f"[view error] unsupported contract: {contract}")])])
    return TextBlock(lines=[Line.plain(line) for line in page_view_lines(payload)])
