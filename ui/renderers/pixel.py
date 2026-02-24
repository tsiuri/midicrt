"""Optional SDL/Pygame pixel renderer backend.

This module is imported only by the run_pixel startup profile so TUI startup
remains free of GUI dependencies.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from configutil import load_section, save_section
from ui.model import Column, Frame, Line, Segment, Spacer, Style, TextBlock, Widget


@dataclass(frozen=True)
class PixelSegment:
    """Pixel-space segment carrying text + style metadata."""

    text: str
    style: Style = field(default_factory=Style)


@dataclass(frozen=True)
class PixelLine:
    """Pixel-space line matching ui.model.Line segment ordering."""

    segments: List[PixelSegment] = field(default_factory=list)


@dataclass(frozen=True)
class PixelFrame:
    """Grid-model frame with fixed cell metrics for widget compatibility."""

    cols: int
    rows: int
    cell_width: int
    cell_height: int
    lines: List[PixelLine] = field(default_factory=list)


class PixelRenderer:
    """Render ui.model widget trees into a pixel surface using pygame."""

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

    def __init__(self, renderer_name: str = "sdl2"):
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
        pix_frame = self._to_pixel_frame(widget, frame)
        self._draw_pixel_frame(pix_frame)
        return [" " * frame.cols for _ in range(frame.rows)]

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
        if isinstance(widget, TextBlock):
            return widget.lines
        if isinstance(widget, Spacer):
            return [Line.plain("") for _ in range(max(0, widget.rows))]
        if isinstance(widget, Column):
            out: list[Line] = []
            for child in widget.children:
                out.extend(self._flatten(child))
            return out
        return [Line.plain(f"[Unsupported widget: {type(widget).__name__}]")]

    def _ensure_display(self, pix_frame: PixelFrame):
        if self._pygame is None:
            try:
                import pygame
            except Exception as exc:
                raise RuntimeError(
                    "Optional pixel stack unavailable. Install extras: pip install 'midicrt[pixel]'"
                ) from exc
            self._pygame = pygame
            pygame.init()
            pygame.font.init()
            self._clock = pygame.time.Clock()

        scale = max(1, int(self._settings.get("scale", 1)))
        width = pix_frame.cols * pix_frame.cell_width * scale
        height = pix_frame.rows * pix_frame.cell_height * scale
        desired_size = (width, height)

        if self._surface is None or self._display_size != desired_size:
            flags = 0
            if self._settings.get("fullscreen"):
                flags |= self._pygame.FULLSCREEN
            self._surface = self._pygame.display.set_mode(desired_size, flags)
            self._pygame.display.set_caption(str(self._settings.get("window_title", "midicrt pixel renderer")))
            self._display_size = desired_size
            size = max(8, int(self._settings.get("font_size", 18)) * scale)
            self._font = self._pygame.font.SysFont(str(self._settings.get("font_name", "monospace")), size)

    def _draw_pixel_frame(self, pix_frame: PixelFrame):
        self._ensure_display(pix_frame)
        pg = self._pygame
        assert self._surface is not None
        assert self._font is not None

        fg = tuple(self._settings["palette"]["foreground"])
        bg = tuple(self._settings["palette"]["background"])
        bold_boost = float(self._settings["palette"].get("bold_boost", 1.25))

        scale = max(1, int(self._settings.get("scale", 1)))
        cell_w = pix_frame.cell_width * scale
        cell_h = pix_frame.cell_height * scale

        for event in pg.event.get():
            if event.type == pg.QUIT:
                pg.display.quit()
                self._surface = None
                return

        self._surface.fill(bg)

        for row_idx, line in enumerate(pix_frame.lines):
            col = 0
            for seg in line.segments:
                if col >= pix_frame.cols:
                    break
                text = (seg.text or "")
                max_len = max(0, pix_frame.cols - col)
                text = text[:max_len]
                seg_fg = fg
                seg_bg = bg
                if seg.style.bold:
                    seg_fg = tuple(min(255, int(c * bold_boost)) for c in seg_fg)
                if seg.style.reverse:
                    seg_fg, seg_bg = seg_bg, seg_fg

                if text:
                    rect = pg.Rect(col * cell_w, row_idx * cell_h, len(text) * cell_w, cell_h)
                    self._surface.fill(seg_bg, rect)
                    glyph = self._font.render(text, True, seg_fg)
                    self._surface.blit(glyph, (col * cell_w, row_idx * cell_h))
                col += len(text)

            if col < pix_frame.cols:
                rect = pg.Rect(col * cell_w, row_idx * cell_h, (pix_frame.cols - col) * cell_w, cell_h)
                self._surface.fill(bg, rect)

        tint = self._settings["palette"].get("crt_tint")
        tint_strength = float(self._settings["palette"].get("crt_tint_strength", 0.0))
        if tint and tint_strength > 0:
            overlay = pg.Surface(self._display_size, pg.SRCALPHA)
            alpha = max(0, min(255, int(255 * tint_strength)))
            overlay.fill((int(tint[0]), int(tint[1]), int(tint[2]), alpha))
            self._surface.blit(overlay, (0, 0))

        pg.display.flip()
        if self._clock is not None:
            self._clock.tick(max(1, int(self._settings.get("target_fps", 60))))
