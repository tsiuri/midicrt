from __future__ import annotations

from engine.page_contracts import build_legacy_page_view_contract
from ui.view_contracts import widget_from_page_view_contract


def build_widget_from_contract(draw_fn, state, draw_line_ref):
    payload = build_legacy_page_view_contract(draw_fn, state, draw_line_ref).as_dict()
    return widget_from_page_view_contract(payload)
