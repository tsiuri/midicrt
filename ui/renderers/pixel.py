"""Optional SDL/Pygame pixel renderer backend.

This module is imported only by the run_pixel startup profile so TUI startup
remains free of GUI dependencies.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from blessed import Terminal

from configutil import load_section, save_section
from ui.model import EventLogWidget, FooterStatusWidget, Frame, Line, NotesWidget, PianoRollWidget, Segment, Style, TransportWidget, Widget
from ui.renderers.text import TextRenderer


@dataclass(frozen=True)
class PixelSegment:
    text: str
    style: Style = field(default_factory=Style)


@dataclass(frozen=True)
class PixelLine:
    segments: list[PixelSegment] = field(default_factory=list)


@dataclass(frozen=True)
class PixelFrame:
    cols: int
    rows: int
    cell_width: int
    cell_height: int
    lines: list[PixelLine] = field(default_factory=list)


class PixelRenderer(TextRenderer):
    """Pixel-oriented renderer with piano-roll parity support."""

    _DEFAULTS = {
        "backend": "sdl2",
        "fullscreen": False,
        "scale": 1,
        "target_fps": 60,
        "font_name": "DejaVu Sans Mono",
        "font_size": 18,
        "cell_width": 10,
        "cell_height": 18,
        "window_title": "midicrt pixel renderer",
        "palette": {
            "foreground": [120, 255, 120],
            "background": [5, 16, 5],
            "bold_boost": 1.25,
            "crt_tint": [30, 255, 100],
            "crt_tint_strength": 0.08,
        },
    }

    def __init__(self, renderer_name: str = "sdl2", term: Terminal | None = None):
        super().__init__(term=term)
        self.renderer_name = (renderer_name or "sdl2").lower()
        self._settings = self._load_settings()
        self._apply_env_overrides()
        self._validate_backend()

        self._pygame = None
        self._surface = None
        self._font = None
        self._clock = None
        self._display_size = (0, 0)

    def render(self, widget: Widget, frame: Frame) -> list[str]:
        text_lines = super().render(widget, frame)
        pix_frame = self._to_pixel_frame(widget, frame)
        if self._draw_pixel_frame(pix_frame):
            return [" " * frame.cols for _ in range(frame.rows)]

        fallback = "[PixelRenderer fallback: text mode]"
        if text_lines:
            text_lines[0] = (fallback + " " * frame.cols)[: frame.cols]
        return text_lines

    def _load_settings(self) -> dict:
        cfg = load_section("pixel_renderer")
        merged = dict(self._DEFAULTS)
        merged["palette"] = dict(self._DEFAULTS["palette"])
        if isinstance(cfg, dict):
            merged.update({k: v for k, v in cfg.items() if k != "palette"})
            if isinstance(cfg.get("palette"), dict):
                merged["palette"].update(cfg["palette"])
        try:
            save_section("pixel_renderer", merged)
        except Exception:
            pass
        return merged

    def _apply_env_overrides(self):
        if "MIDICRT_PIXEL_SCALE" in os.environ:
            self._settings["scale"] = int(os.environ["MIDICRT_PIXEL_SCALE"])
        if "MIDICRT_PIXEL_TARGET_FPS" in os.environ:
            self._settings["target_fps"] = int(os.environ["MIDICRT_PIXEL_TARGET_FPS"])
        if "MIDICRT_PIXEL_FULLSCREEN" in os.environ:
            v = os.environ["MIDICRT_PIXEL_FULLSCREEN"].strip().lower()
            self._settings["fullscreen"] = v in {"1", "true", "yes", "on"}
        tint = os.environ.get("MIDICRT_PIXEL_CRT_TINT")
        if tint:
            parts = [int(p.strip()) for p in tint.split(",") if p.strip()]
            if len(parts) == 3:
                self._settings["palette"]["crt_tint"] = parts

    def _validate_backend(self):
        if self.renderer_name not in {"sdl2", "pygame", "kmsdrm", "fb", "framebuffer"}:
            raise RuntimeError(f"Unsupported pixel renderer backend: {self.renderer_name}")

    def _to_pixel_frame(self, widget: Widget, frame: Frame) -> PixelFrame:
        lines = self._flatten(widget)
        out_lines: list[PixelLine] = []
        for line in lines[: frame.rows]:
            out_lines.append(PixelLine([PixelSegment(s.text, s.style) for s in line.segments]))
        while len(out_lines) < frame.rows:
            out_lines.append(PixelLine([PixelSegment("")]))
        return PixelFrame(
            cols=frame.cols,
            rows=frame.rows,
            cell_width=max(1, int(self._settings["cell_width"])),
            cell_height=max(1, int(self._settings["cell_height"])),
            lines=out_lines,
        )

    def _flatten(self, widget: Widget) -> list[Line]:
        if isinstance(widget, PianoRollWidget):
            return self._flatten_pianoroll(widget)
        return super()._flatten(widget)

    def _flatten_pianoroll(self, widget: PianoRollWidget) -> list[Line]:
        lines: list[Line] = []
        timeline_label = f"{'Bars':>7} │"
        lines.append(Line.plain(timeline_label + widget.timeline))
        if not widget.pitches or not widget.cells:
            lines.append(Line.plain("Piano Roll: unavailable"))
            return lines
        for pitch, row_cells in zip(widget.pitches, widget.cells):
            label = f"{self._note_name(pitch):>7} │"
            chars = "".join(self._pixel_char(c.velocity, c.channel, widget.style_mode) for c in row_cells)
            lines.append(Line.plain(label + chars))
        return lines

    def _pixel_char(self, velocity: int, channel: int | None, style_mode: str) -> str:
        if velocity <= 0:
            return " "
        if style_mode == "text":
            return self._velocity_char(velocity)

        base = "█"
        if getattr(self.term, "number_of_colors", 0) and channel is not None:
            palette = [2, 3, 4, 5, 6, 7, 1]
            color = palette[(int(channel) - 1) % len(palette)]
            return self.term.color(color)(base)
        shades = ["█", "▓", "▒", "░"]
        if channel is None:
            return base
        return shades[(int(channel) - 1) % len(shades)]

    def _ensure_display(self, pix_frame: PixelFrame):
        if self._pygame is not None:
            return
        try:
            import pygame
        except Exception as exc:
            raise RuntimeError(
                "Optional pixel stack unavailable. Install extras: pip install 'midicrt[pixel]'"
            ) from exc

        self._pygame = pygame
        pygame.init()
        width = pix_frame.cols * pix_frame.cell_width * max(1, int(self._settings["scale"]))
        height = pix_frame.rows * pix_frame.cell_height * max(1, int(self._settings["scale"]))
        self._surface = pygame.display.set_mode((width, height))
        pygame.display.set_caption(str(self._settings.get("window_title", "midicrt pixel renderer")))
        self._clock = pygame.time.Clock()
        self._display_size = (width, height)

    def _draw_pixel_frame(self, pix_frame: PixelFrame) -> bool:
        try:
            self._ensure_display(pix_frame)
        except RuntimeError:
            return False

        assert self._pygame is not None
        assert self._surface is not None
        fg = tuple(self._settings["palette"].get("foreground", [120, 255, 120]))
        bg = tuple(self._settings["palette"].get("background", [5, 16, 5]))
        self._surface.fill(bg)

        # Transitional MVP: flatten text into the pixel surface.
        scale = max(1, int(self._settings["scale"]))
        font_size = max(8, int(self._settings["font_size"])) * scale
        font = self._pygame.font.SysFont(self._settings.get("font_name", "monospace"), font_size)
        y = 0
        for line in pix_frame.lines:
            text = "".join(seg.text for seg in line.segments)
            if text:
                glyph = font.render(self.term.strip_seqs(text), True, fg)
                self._surface.blit(glyph, (0, y))
            y += pix_frame.cell_height * scale

        self._pygame.display.flip()
        if self._clock:
            self._clock.tick(max(1, int(self._settings["target_fps"])))
        return True
