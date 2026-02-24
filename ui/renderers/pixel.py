"""Optional pixel renderer backend.

This module is intentionally imported only by the run_pixel profile.
"""

from blessed import Terminal

from ui.model import Line, PianoRollWidget
from ui.renderers.text import TextRenderer


class PixelRenderer(TextRenderer):
    """Pixel-oriented renderer with piano-roll parity support."""

    def __init__(self, renderer_name: str = "sdl2"):
        self.renderer_name = renderer_name
        self._validate_optional_stack(renderer_name)
        super().__init__(Terminal())

    @staticmethod
    def _validate_optional_stack(renderer_name: str):
        name = (renderer_name or "sdl2").lower()
        if name in {"sdl2", "kmsdrm", "framebuffer", "fb"}:
            try:
                import pygame  # noqa: F401
            except Exception as exc:
                raise RuntimeError(
                    "Optional pixel stack unavailable. Install extras: pip install 'midicrt[pixel]'"
                ) from exc

    def _flatten(self, widget):
        if isinstance(widget, PianoRollWidget):
            return self._flatten_pianoroll(widget)
        return super()._flatten(widget)

    def _flatten_pianoroll(self, widget: PianoRollWidget) -> list[Line]:
        lines: list[Line] = []
        timeline_label = f"{'Bars':>7} │"
        lines.append(Line.plain(timeline_label + widget.timeline))
        for pitch, row_cells in zip(widget.pitches, widget.cells):
            label = f"{self._note_name(pitch):>7} │"
            chars = "".join(self._pixel_char(c.velocity, c.channel, widget.style_mode) for c in row_cells)
            lines.append(Line.plain(label + chars))
        return lines

    def _pixel_char(self, velocity: int, channel: int | None, style_mode: str) -> str:
        if velocity <= 0:
            return " "
        # text mode keeps parity with terminal glyph density.
        if style_mode == "text":
            return self._velocity_char(velocity)

        # dense mode favors filled cells while remaining monochrome-compatible.
        base = "█"
        if getattr(self.term, "number_of_colors", 0) and channel is not None:
            palette = [2, 3, 4, 5, 6, 7, 1]
            color = palette[(int(channel) - 1) % len(palette)]
            return self.term.color(color)(base)
        # fallback shading by channel when color isn't available.
        shades = ["█", "▓", "▒", "░"]
        if channel is None:
            return base
        return shades[(int(channel) - 1) % len(shades)]
