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
    Compositor, GREEN_BRIGHT, GREEN_MID, GREEN_DIM, _rgb565,
)
from ui.model import Column, Frame, PianoRollWidget, Spacer, TextBlock, Widget
from ui.renderers.text import TextRenderer

BG = _rgb565(0, 8, 2)   # very dark green background

# PAGE_Y_OFFSET: rows 0-2 are header / transport / blank in midicrt
PAGE_Y_OFFSET = 3

# Per-channel note-bar colours (16 MIDI channels)
_CH_BASE_RGB = [(0, 255, 80)] * 16
_CH_COLOURS = [_rgb565(*rgb) for rgb in _CH_BASE_RGB]


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
        self.comp = Compositor(bg=BG)
        self._badge_frames = None  # lazily pre-computed bar animation
        # Pre-compute velocity-scaled RGB565 colours for piano roll cells.
        self._vel_lut = []
        for base_rgb in _CH_BASE_RGB:
            lut = np.zeros(128, dtype=np.uint16)
            for v in range(128):
                scale = 0.4 + 0.6 * (v / 127.0)
                r, g, b = (int(c * scale) for c in base_rgb)
                lut[v] = _rgb565(r, g, b)
            self._vel_lut.append(lut)

    # ------------------------------------------------------------------
    # Frame lifecycle
    # ------------------------------------------------------------------

    def frame_clear(self) -> None:
        """Fill the PIL buffer with the background colour."""
        self.comp.clear()

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
        bg = _rgb565(0, 20, 8)
        bar_hi = _rgb565(0, 255, 80)
        bar_mid = _rgb565(0, 180, 60)
        bar_lo = _rgb565(0, 120, 40)
        border = _rgb565(0, 180, 50)

        frames = []
        n_frames = 48  # 2 seconds at 24fps
        for fi in range(n_frames):
            t = fi * (2.0 * np.pi / 5.0) / n_frames  # one sine period
            region = np.full((anim_h, bw), bg, dtype=np.uint16)
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
            inner[rows == tops]                         = bar_hi
            inner[(rows > tops)  & (rows < mids)]       = bar_mid
            inner[(rows >= mids) & (rows < ends)]       = bar_lo
            region[0,  :]  = border
            region[-1, :]  = border
            region[:,  0]  = border
            region[:, -1]  = border
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
        comp.rect(x, badge_y, bw, bh, _rgb565(0, 30, 10))
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
        cw, cell_h = comp.char_w, comp.char_h
        LEFT_CHARS = 10   # matches pianoroll.py LEFT_MARGIN

        # --- Timeline row ---
        px_y = (PAGE_Y_OFFSET + y_row) * cell_h
        comp.text(0, px_y, f"{'Bars':>7} \u2502", fg=GREEN_DIM)
        roll_cols = len(widget.timeline)
        ticks_per_col = max(1, int(getattr(widget, "ticks_per_col", 1)))
        tick_anchor = int(getattr(widget, "tick_now", getattr(widget, "tick_right", 0)))
        tick_left = tick_anchor - max(1, roll_cols - 1) * ticks_per_col
        tick_right_edge = tick_anchor + ticks_per_col
        if roll_cols > 0:
            px_per_tick = cw / ticks_per_col
            x_left = LEFT_CHARS * cw
            x_right = x_left + roll_cols * cw
            bar_ticks = 24 * 4
            beat_ticks = 24
            first_bar = ((tick_left + bar_ticks - 1) // bar_ticks) * bar_ticks
            first_beat = ((tick_left + beat_ticks - 1) // beat_ticks) * beat_ticks
            t = first_bar
            while t <= tick_right_edge:
                px_x = x_left + int(round((t - tick_left) * px_per_tick))
                if x_left <= px_x < x_right:
                    comp.rect(px_x, px_y, 1, cell_h, GREEN_MID)
                t += bar_ticks
            t = first_beat
            while t <= tick_right_edge:
                if t % bar_ticks != 0:
                    px_x = x_left + int(round((t - tick_left) * px_per_tick))
                    if x_left <= px_x < x_right:
                        comp.rect(px_x, px_y, 1, cell_h, GREEN_DIM)
                t += beat_ticks
        y_row += 1

        pitch_high = widget.pitch_high if widget.pitch_high else (widget.pitches[0] if widget.pitches else None)
        pitch_low = widget.pitch_low if widget.pitch_low else (widget.pitches[-1] if widget.pitches else None)
        roll_top_px = (PAGE_Y_OFFSET + y_row) * cell_h
        x0 = LEFT_CHARS * cw

        spans = getattr(widget, "spans", None) or []
        columns = getattr(widget, "columns", None) or []

        # Pitches that currently have any visible bar get reverse labels.
        highlight_pitches = set()
        if pitch_high is not None and pitch_low is not None:
            if spans:
                for start_tick, end_tick, pitch, _channel, velocity in spans:
                    if velocity <= 0:
                        continue
                    if pitch > pitch_high or pitch < pitch_low:
                        continue
                    if end_tick < tick_left or start_tick > tick_right_edge:
                        continue
                    highlight_pitches.add(int(pitch))
            elif columns:
                for col_events in columns:
                    for pitch, _channel, velocity in col_events:
                        if velocity <= 0:
                            continue
                        if pitch_high >= pitch >= pitch_low:
                            highlight_pitches.add(int(pitch))

        # --- Note rows ---
        NOTE_NAMES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
        for pitch in widget.pitches:
            if y_row >= frame.rows:
                break
            px_y = (PAGE_Y_OFFSET + y_row) * cell_h

            note_name = f"{NOTE_NAMES[pitch % 12]}{(pitch // 12) - 1}"
            is_c = (pitch % 12 == 0)
            label = f"{note_name:>7} \u2502"
            if int(pitch) in highlight_pitches:
                comp.rect(0, px_y, LEFT_CHARS * cw, cell_h, GREEN_MID)
                comp.text(0, px_y, label, fg=BG)
            else:
                comp.text(
                    0, px_y,
                    label,
                    fg=GREEN_BRIGHT if is_c else GREEN_DIM,
                )
            y_row += 1

        # --- Note bars (continuous spans preferred) ---
        roll_top_px = (PAGE_Y_OFFSET + (y_row - len(widget.pitches))) * cell_h
        if spans and pitch_high is not None and pitch_low is not None:
            roll_cols = len(widget.timeline)
            ticks_per_col = max(1, int(getattr(widget, "ticks_per_col", 1)))
            tick_anchor = int(getattr(widget, "tick_now", getattr(widget, "tick_right", 0)))
            tick_left = tick_anchor - max(1, roll_cols - 1) * ticks_per_col
            tick_right_edge = tick_anchor + ticks_per_col
            px_per_tick = cw / ticks_per_col
            x_left = x0
            x_right = x0 + roll_cols * cw

            for start_tick, end_tick, pitch, channel, velocity in spans:
                if velocity <= 0:
                    continue
                if pitch > pitch_high or pitch < pitch_low:
                    continue
                if end_tick < tick_left or start_tick > tick_right_edge:
                    continue
                row_idx = pitch_high - pitch
                px_y = roll_top_px + row_idx * cell_h + 1
                px_start = x_left + int(round((start_tick - tick_left) * px_per_tick))
                px_end = x_left + int(round((end_tick - tick_left) * px_per_tick))
                if px_end < px_start:
                    px_start, px_end = px_end, px_start
                px_start = max(x_left, min(x_right, px_start))
                px_end = max(x_left, min(x_right, px_end))
                width = max(5, px_end - px_start)
                if px_start >= x_right or px_end <= x_left:
                    continue
                if px_start + width > x_right:
                    width = max(1, x_right - px_start)
                ch_idx = (int(channel) - 1) if channel is not None else 0
                color = self._vel_lut[ch_idx % 16][min(int(velocity), 127)]
                comp.rect(px_start, px_y, width, cell_h - 2, color)
        else:
            if columns and pitch_high is not None and pitch_low is not None:
                for i, col_events in enumerate(columns):
                    if not col_events:
                        continue
                    px_x = x0 + i * cw + 1
                    for pitch, channel, velocity in col_events:
                        if velocity <= 0:
                            continue
                        if pitch > pitch_high or pitch < pitch_low:
                            continue
                        row_idx = pitch_high - pitch
                        px_y = roll_top_px + row_idx * cell_h + 1
                        ch_idx = (int(channel) - 1) if channel is not None else 0
                        color = self._vel_lut[ch_idx % 16][min(int(velocity), 127)]
                        comp.rect(px_x, px_y, cw - 1, cell_h - 2, color)
            else:
                # Fallback: dense grid rendering (legacy widget.cells)
                for row_idx, row_cells in enumerate(widget.cells):
                    px_y = roll_top_px + row_idx * cell_h
                    for i, cell in enumerate(row_cells):
                        if cell.velocity > 0:
                            ch_idx = (int(cell.channel) - 1) if cell.channel is not None else 0
                            color = self._vel_lut[ch_idx % 16][min(cell.velocity, 127)]
                            comp.rect(x0 + i * cw + 1, px_y + 1, cw - 1, cell_h - 2, color)

        return y_row

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        self.comp.close()
