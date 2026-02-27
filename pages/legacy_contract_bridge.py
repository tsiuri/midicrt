from __future__ import annotations

from engine.page_contracts import capture_legacy_page_view
from ui.view_contracts import widget_from_page_view


def build_widget_from_legacy_contract(draw_fn, state, draw_line_ref):
    payload = capture_legacy_page_view(draw_fn, state, draw_line_ref).to_dict()
    return widget_from_page_view(payload)
