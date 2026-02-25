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
import math
import time

import numpy as np

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
        self._badge_frames = None  # lazily pre-computed bar animation

    # ------------------------------------------------------------------
    # Frame lifecycle
    # ------------------------------------------------------------------

    def frame_clear(self) -> None:
        """Fill the PIL buffer with the background colour."""
        self.comp.clear(BG)

    def frame_flush(self) -> None:
        """Convert PIL buffer → RGB565 and write to /dev/fb0."""
        self.comp.flush()

    def _build_badge_frames(self) -> None:
        """Pre-compute 48 frames (2s loop) of the scrolling bar animation."""
        cw, ch = self.comp.char_w, self.comp.char_h
        label = "welcome to the jungle ^_^"
        bw = (len(label) + 2) * cw
        anim_h = 5 * ch
        max_bar = anim_h - 2
        xi_f = np.arange(1, bw - 1, dtype=np.float32)
        rows = np.arange(anim_h)[:, np.newaxis]

        frames = []
        n_frames = 48  # 2 seconds at 24fps
        for fi in range(n_frames):
            t = fi * (2.0 * np.pi / 5.0) / n_frames  # one sine period
            region = np.full((anim_h, bw, 3), [0, 20, 8], dtype=np.uint8)
            phase = np.float32(t * 5.0) + xi_f * np.float32(0.22)
            h_arr = np.clip(
                np.abs(np.sin(phase))           * np.float32(0.50) +
                np.abs(np.sin(phase * np.float32(2.3) + np.float32(1.1))) * np.float32(0.30) +
                np.abs(np.sin(phase * np.float32(0.7) + np.float32(2.5))) * np.float32(0.20),
                np.float32(1.0 / max_bar), np.float32(1.0)
            )
            h_arr = np.maximum(1, (h_arr * max_bar).astype(np.int32))
            top_arr = (1 + max_bar - h_arr)
            mid_arr = top_arr + np.maximum(1, h_arr // 2)
            tops  = top_arr[np.newaxis, :]
            mids  = mid_arr[np.newaxis, :]
            ends  = (top_arr + h_arr)[np.newaxis, :]
            inner = region[:, 1:-1]
            inner[rows == tops]                         = [0, 255,  80]
            inner[(rows > tops)  & (rows < mids)]       = [0, 180,  60]
            inner[(rows >= mids) & (rows < ends)]       = [0,  80,  30]
            region[0,  :]  = [0, 180, 50]
            region[-1, :]  = [0, 180, 50]
            region[:,  0]  = [0, 180, 50]
            region[:, -1]  = [0, 180, 50]
            frames.append(region)
        self._badge_frames = frames
        self._badge_idx = 0

    def draw_notes_badge(self) -> None:
        """Animated scrolling bars + 'welcome to the jungle ^_^' badge, bottom-right."""
        comp = self.comp
        cw, ch = comp.char_w, comp.char_h
        label = "welcome to the jungle ^_^"
        bw = (len(label) + 2) * cw
        bh = 3 * ch
        anim_h = 5 * ch
        x = 800 - bw - 4
        badge_y = 475 - bh - 4
        anim_y = badge_y - anim_h

        # --- Scrolling bars (pre-computed, just copy) ---
        if self._badge_frames is None:
            self._build_badge_frames()
        comp._buf[anim_y:anim_y + anim_h, x:x + bw] = self._badge_frames[self._badge_idx]
        self._badge_idx = (self._badge_idx + 1) % len(self._badge_frames)

        # --- Badge box ---
        comp.rect(x, badge_y, bw, bh, (0, 30, 10))
        comp.rect(x, badge_y, bw, 1, GREEN_MID)
        comp.rect(x, badge_y + bh - 1, bw, 1, GREEN_MID)
        comp.rect(x, badge_y, 1, bh, GREEN_MID)
        comp.rect(x + bw - 1, badge_y, 1, bh, GREEN_MID)
        comp.text(x + cw, badge_y + ch, label, fg=GREEN_BRIGHT)

    # ------------------------------------------------------------------
    # Line-level drawing (header / transport rows, drawn before render())
    # ------------------------------------------------------------------

    def draw_text_line(self, row: int, text: str) -> None:
        """Render a plain text line at character-row 'row'."""
        # Fast path: skip regex for text with no ANSI escape sequences
        if '\x1b' in text:
            plain = self.term.strip_seqs(text).rstrip()
        else:
            plain = text.rstrip()
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
