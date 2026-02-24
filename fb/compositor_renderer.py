"""fb/compositor_renderer.py — Full-system compositor renderer for midicrt.

Routes all midicrt rendering through fb/compositor (PIL → /dev/fb0)
instead of the terminal.  All pages using build_widget() get pixel
rendering automatically.  Pages using the legacy draw() path still
work but continue writing to the terminal (harmless while nothing
else is printing to tty1).

No KD_GRAPHICS / vt-mode: proved to break x11vnc on vc4-fkms-v3d.

Usage in midicrt:
    configure_startup_profile("run_compositor")
"""

from __future__ import annotations

from fb.compositor import (
    Compositor, GREEN_BRIGHT, GREEN_MID, GREEN_DIM, BLACK,
)
from ui.model import Column, Frame, PianoRollWidget, Spacer, TextBlock, Widget
from ui.renderers.text import TextRenderer

BG = (0, 8, 2)   # very dark green background

# PAGE_Y_OFFSET: rows 0-2 are header / transport / blank in midicrt
PAGE_Y_OFFSET = 3

# Per-channel note-bar colours (16 MIDI channels)
_CH_COLOURS = [
    (0, 220, 80),    (0, 160, 220),  (220, 180, 0),  (220, 60, 0),
    (180, 0, 220),   (0, 220, 180),  (220, 0, 100),  (100, 220, 0),
    (220, 120, 0),   (0, 100, 220),  (160, 220, 0),  (220, 0, 160),
    (0, 220, 120),   (140, 0, 220),  (220, 140, 0),  (0, 180, 220),
]


class CompositorRenderer(TextRenderer):
    """Routes all midicrt rendering through fb/compositor (PIL → fb0).

    midicrt's ui_loop calls:
        renderer.frame_clear()      — at the start of each frame
        draw_line(row, text)        — for header / transport rows (0-2)
        renderer.render(widget, frame) — for page content (rows 3+)
        renderer.frame_flush()      — at the end of each frame

    Overlays (screensaver blanking, bouncing shapes, HUD) can be drawn
    directly on self.comp between render() and frame_flush().
    """

    def __init__(self) -> None:
        super().__init__()
        self.comp = Compositor()

    # ------------------------------------------------------------------
    # Frame lifecycle
    # ------------------------------------------------------------------

    def frame_clear(self) -> None:
        """Fill the PIL buffer with the background colour."""
        self.comp.clear(BG)

    def frame_flush(self) -> None:
        """Convert PIL buffer → RGB565 and write to /dev/fb0."""
        self.comp.flush()

    # ------------------------------------------------------------------
    # Line-level drawing (header / transport rows, drawn before render())
    # ------------------------------------------------------------------

    def draw_text_line(self, row: int, text: str) -> None:
        """Render a plain text line at character-row 'row'."""
        plain = self.term.strip_seqs(text).rstrip()
        if plain:
            self.comp.text(0, row * self.comp.char_h, plain, fg=GREEN_BRIGHT)

    # ------------------------------------------------------------------
    # Renderer protocol
    # ------------------------------------------------------------------

    def render(self, widget: Widget, frame: Frame) -> list[str]:
        """Draw the widget tree into the compositor buffer.

        Returns a list of empty strings so that midicrt's subsequent
        draw_line(3+idx, line) calls are no-ops (the compositor has
        already drawn everything).
        """
        self._render_widget(widget, frame, y_row=0)
        return [""] * frame.rows

    # ------------------------------------------------------------------
    # Widget rendering
    # ------------------------------------------------------------------

    def _render_widget(self, widget: Widget, frame: Frame, y_row: int) -> int:
        """Recursively render a widget into the compositor buffer.

        y_row is in content-area row coordinates (0 = first row below
        the header block, i.e. pixel row PAGE_Y_OFFSET * char_h).
        Returns the next free y_row.
        """
        if isinstance(widget, TextBlock):
            for line in widget.lines:
                if y_row >= frame.rows:
                    break
                plain = self.term.strip_seqs(
                    self._render_line(line, frame.cols)
                ).rstrip()
                if plain:
                    px_y = (PAGE_Y_OFFSET + y_row) * self.comp.char_h
                    self.comp.text(0, px_y, plain, fg=GREEN_BRIGHT)
                y_row += 1
            return y_row

        if isinstance(widget, Spacer):
            return y_row + max(0, widget.rows)

        if isinstance(widget, Column):
            for child in widget.children:
                y_row = self._render_widget(child, frame, y_row)
            return y_row

        if isinstance(widget, PianoRollWidget):
            return self._render_pianoroll(widget, frame, y_row)

        # Unknown widget type — skip one row
        return y_row + 1

    def _render_pianoroll(
        self, widget: PianoRollWidget, frame: Frame, y_row: int
    ) -> int:
        """Render the piano roll with pixel-resolution coloured note bars."""
        comp = self.comp
        cw, ch = comp.char_w, comp.char_h
        LEFT_CHARS = 10   # matches pianoroll.py LEFT_MARGIN

        # --- Timeline row ---
        px_y = (PAGE_Y_OFFSET + y_row) * ch
        comp.text(0, px_y, f"{'Bars':>7} \u2502", fg=GREEN_DIM)
        for i, mark in enumerate(widget.timeline):
            if mark.strip():
                fg = GREEN_MID if mark == "|" else GREEN_DIM
                comp.text(LEFT_CHARS * cw + i * cw, px_y, mark, fg=fg)
        y_row += 1

        # --- Note rows ---
        NOTE_NAMES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
        for pitch, row_cells in zip(widget.pitches, widget.cells):
            if y_row >= frame.rows:
                break
            px_y = (PAGE_Y_OFFSET + y_row) * ch

            note_name = f"{NOTE_NAMES[pitch % 12]}{(pitch // 12) - 1}"
            is_c = (pitch % 12 == 0)
            comp.text(
                0, px_y,
                f"{note_name:>7} \u2502",
                fg=GREEN_BRIGHT if is_c else GREEN_DIM,
            )

            x0 = LEFT_CHARS * cw
            for i, cell in enumerate(row_cells):
                if cell.velocity > 0:
                    ch_idx = (int(cell.channel) - 1) if cell.channel is not None else 0
                    base = _CH_COLOURS[ch_idx % len(_CH_COLOURS)]
                    scale = 0.4 + 0.6 * (cell.velocity / 127.0)
                    color = tuple(int(c * scale) for c in base)
                    # Inset 1 px to keep a visible grid gap
                    comp.rect(x0 + i * cw + 1, px_y + 1, cw - 1, ch - 2, color)

            y_row += 1

        return y_row

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        self.comp.close()
