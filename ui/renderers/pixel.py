"""Optional SDL/Pygame pixel renderer backend.

This module is imported only by the run_pixel startup profile so TUI startup
remains free of GUI dependencies.
"""

from blessed import Terminal

from ui.model import Line, PianoRollWidget
from ui.renderers.text import TextRenderer


class PixelRenderer(TextRenderer):
    """Pixel-oriented renderer with piano-roll parity support."""

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
