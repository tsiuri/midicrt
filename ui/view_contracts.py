from __future__ import annotations

from typing import Any

from ui.model import Column, Line, TextBlock, Widget


def widget_from_page_view_contract(payload: dict[str, Any]) -> Widget:
    """Convert a contract payload to a text widget."""

    contract = str(payload.get("contract", ""))
    if contract != "legacy.page.view":
        return Column(children=[TextBlock(lines=[Line.plain(f"[view error] unsupported contract: {contract}")])])

    return TextBlock(lines=[Line.plain(text) for text in lines_from_page_view_contract(payload)])


def lines_from_page_view_contract(payload: dict[str, Any]) -> list[str]:
    contract = str(payload.get("contract", ""))
    if contract != "legacy.page.view":
        return []
    lines = payload.get("lines", [])
    if not isinstance(lines, list):
        return []
    return [str(text) for text in lines]
