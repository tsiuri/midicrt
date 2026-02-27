"""Plugin overlay capture + composition helpers."""

from __future__ import annotations

import sys
from typing import Iterable

from fb.terminal_capture import TerminalCapture
from ui.model import OverlayEntry, OverlayLayerWidget


def capture_plugin_overlay_widget(
    plugins: Iterable[object],
    state: dict,
    cols: int,
    rows: int,
    draw_takes_state: dict[int, bool],
) -> OverlayLayerWidget:
    """Capture plugin draw output into deterministic overlay entries."""
    entries: list[OverlayEntry] = []
    for z_index, mod in enumerate(plugins):
        draw = getattr(mod, "draw", None)
        if draw is None:
            continue
        cap = TerminalCapture(cols, rows)
        saved = sys.stdout
        sys.stdout = cap
        try:
            if draw_takes_state.get(id(mod), False):
                draw(state)
            else:
                draw()
        except Exception:
            pass
        finally:
            sys.stdout = saved

        plugin_id = getattr(mod, "__name__", mod.__class__.__name__)
        for row, text in cap.rows_with_content():
            entries.append(
                OverlayEntry(
                    plugin_id=plugin_id,
                    z_index=z_index,
                    row=int(row),
                    col=0,
                    kind="badge",
                    text=text,
                )
            )
    entries.sort(key=lambda e: (e.z_index, e.row, e.col, e.plugin_id))
    return OverlayLayerWidget(entries=entries)


def compose_overlay_rows(
    overlay: OverlayLayerWidget,
    cols: int,
    rows: int,
    start_row: int = 0,
) -> list[tuple[int, str]]:
    """Compose overlay entries into final rows honoring z-index ordering."""
    if cols <= 0 or rows <= 0:
        return []
    grid = [[" "] * cols for _ in range(rows)]
    for ent in sorted(overlay.entries, key=lambda e: (e.z_index, e.row, e.col, e.plugin_id)):
        row = int(ent.row)
        col = max(0, int(ent.col))
        if row < 0 or row >= rows:
            continue
        text = str(ent.text or "")
        for idx, ch in enumerate(text):
            c = col + idx
            if c >= cols:
                break
            grid[row][c] = ch

    out: list[tuple[int, str]] = []
    for row_idx in range(max(0, int(start_row)), rows):
        line = "".join(grid[row_idx]).rstrip()
        if line:
            out.append((row_idx, line))
    return out
