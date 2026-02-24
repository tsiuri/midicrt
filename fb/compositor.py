"""fb/compositor.py — Full-screen framebuffer compositor.

Writes directly to /dev/fb0 (RGB565) using a PIL Image render buffer.
All output — text and graphics — is composited in-memory and flushed
to fb0 each frame. No KD_GRAPHICS / vt-mode switching is used because
on vc4-fkms-v3d (Raspberry Pi Fake KMS) that call breaks x11vnc's
view of the framebuffer.

Caller is responsible for silencing the kernel console (fbcon) before
using this: the natural way is to stop any process that writes to tty1
(e.g. the terminal app itself switches to compositor mode and stops
printing to the terminal).

Requires: Pillow, numpy

Typical usage:
    comp = Compositor()
    comp.clear()
    comp.text(0, 0, "Hello", fg=(0, 255, 80))
    comp.rect(80, 40, 400, 8, (0, 60, 20))
    comp.flush()
    ...
    comp.close()   # or just let atexit handle it
"""

import atexit
import mmap
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# --- Framebuffer constants ---
FB_PATH  = "/dev/fb0"
FB_W     = 800
FB_H     = 475
FB_SIZE  = FB_W * FB_H * 2   # RGB565: 2 bytes/pixel

# --- Default font ---
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_SIZE = 13   # ~7×13 px per cell → ~114 cols × 36 rows

# --- CRT green palette ---
GREEN_BRIGHT = (0, 255, 80)
GREEN_MID    = (0, 180, 50)
GREEN_DIM    = (0, 100, 30)
BLACK        = (0, 0, 0)


class Compositor:
    """Pixel-level compositor backed by /dev/fb0.

    All coordinates are in pixels. Text is rendered with a TrueType font
    via Pillow. Graphics (rects, lines, arbitrary pixel writes) use the
    same PIL Image buffer. flush() converts to RGB565 and writes to fb0.
    """

    def __init__(
        self,
        font_path: str = FONT_PATH,
        font_size: int = FONT_SIZE,
        bg: tuple = BLACK,
    ) -> None:
        # Primary render buffer
        self._img  = Image.new("RGB", (FB_W, FB_H), bg)
        self._draw = ImageDraw.Draw(self._img)

        # Font
        try:
            self._font = ImageFont.truetype(font_path, font_size)
        except Exception:
            self._font = ImageFont.load_default()

        # Measure character cell (monospace — use 'M')
        bb = self._draw.textbbox((0, 0), "M", font=self._font)
        self.char_w = max(1, bb[2] - bb[0])
        self.char_h = max(1, bb[3] - bb[1])
        self.cols   = FB_W // self.char_w
        self.rows   = FB_H // self.char_h

        # Open framebuffer
        self._fb_file = open(FB_PATH, "r+b", buffering=0)
        self._fb_mm   = mmap.mmap(self._fb_file.fileno(), FB_SIZE)

        self._closed = False
        atexit.register(self.close)

    # ------------------------------------------------------------------
    # Drawing primitives
    # ------------------------------------------------------------------

    def clear(self, rgb: tuple = BLACK) -> None:
        """Fill the entire buffer with a colour."""
        self._draw.rectangle((0, 0, FB_W - 1, FB_H - 1), fill=rgb)

    def rect(self, x: int, y: int, w: int, h: int, rgb: tuple) -> None:
        """Draw a filled rectangle (pixel coords)."""
        self._draw.rectangle((x, y, x + w - 1, y + h - 1), fill=rgb)

    def line(self, x0: int, y0: int, x1: int, y1: int, rgb: tuple, width: int = 1) -> None:
        """Draw a line."""
        self._draw.line((x0, y0, x1, y1), fill=rgb, width=width)

    def text(
        self,
        x: int,
        y: int,
        s: str,
        fg: tuple = GREEN_BRIGHT,
        bg: tuple | None = None,
    ) -> None:
        """Draw text at pixel coordinates, with optional background."""
        if bg is not None:
            bb = self._draw.textbbox((x, y), s, font=self._font)
            self._draw.rectangle(bb, fill=bg)
        self._draw.text((x, y), s, font=self._font, fill=fg)

    def text_cell(
        self,
        col: int,
        row: int,
        s: str,
        fg: tuple = GREEN_BRIGHT,
        bg: tuple | None = None,
    ) -> None:
        """Draw text at character-cell coordinates."""
        self.text(col * self.char_w, row * self.char_h, s, fg=fg, bg=bg)

    def pixel(self, x: int, y: int, rgb: tuple) -> None:
        """Set a single pixel."""
        self._img.putpixel((x, y), rgb)

    def image(self, x: int, y: int, img: Image.Image) -> None:
        """Composite a PIL Image at (x, y). Supports RGBA for alpha blending."""
        self._img.paste(img, (x, y), mask=img if img.mode == "RGBA" else None)

    # ------------------------------------------------------------------
    # Flush to hardware
    # ------------------------------------------------------------------

    def flush(self) -> None:
        """Convert buffer to RGB565 and DMA-write to /dev/fb0."""
        arr   = np.asarray(self._img, dtype=np.uint16)
        rgb565 = (
            ((arr[:, :, 0] & 0xF8) << 8) |
            ((arr[:, :, 1] & 0xFC) << 3) |
            (arr[:, :, 2] >> 3)
        ).astype("<u2")
        self._fb_mm[:] = rgb565.tobytes()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Release fb0."""
        if self._closed:
            return
        self._closed = True
        try:
            self._fb_mm.close()
            self._fb_file.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def rgb(r: int, g: int, b: int) -> tuple:
        return (r, g, b)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
