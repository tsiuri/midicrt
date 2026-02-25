"""fb/compositor.py — Full-screen framebuffer compositor.

Writes directly to /dev/fb0 (RGB565) using a numpy uint8 render buffer.
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

import numpy as np
from PIL import Image

from fb.psf_font import PSFFont

# --- Framebuffer constants ---
FB_PATH  = "/dev/fb0"
FB_W     = 800
FB_H     = 475
FB_SIZE  = FB_W * FB_H * 2   # RGB565: 2 bytes/pixel

# --- CRT green palette ---
GREEN_BRIGHT = (0, 255, 80)
GREEN_MID    = (0, 180, 50)
GREEN_DIM    = (0, 100, 30)
BLACK        = (0, 0, 0)


class Compositor:
    """Pixel-level compositor backed by /dev/fb0.

    Uses a numpy (H, W, 3) uint8 array as the primary render buffer so
    that text, rects, and the flush RGB565 conversion all operate
    directly on numpy memory — eliminating the costly PIL→numpy copy
    that np.asarray(PIL.Image) previously required each frame.

    PSFFont text is rendered via draw_text_buf() directly into the numpy
    buffer using glyph bitmask indexing (no PIL paste overhead).
    flush() converts the buffer to RGB565 and writes to /dev/fb0.
    """

    def __init__(self, bg: tuple = BLACK) -> None:
        # Load the console bitmap font (VGA 8×8)
        self._psf    = PSFFont()
        self.char_w  = self._psf.width
        self.char_h  = self._psf.height
        self.cols    = FB_W // self.char_w
        self.rows    = FB_H // self.char_h

        # Primary render buffer — numpy owns the memory
        self._buf = np.zeros((FB_H, FB_W, 3), dtype=np.uint8)
        self._buf[:, :, 0] = bg[0]
        self._buf[:, :, 1] = bg[1]
        self._buf[:, :, 2] = bg[2]
        # Pre-filled template for fast clear() via np.copyto (avoids slow broadcast)
        self._bg    = bg
        self._clear_tpl = self._buf.copy()

        # Open framebuffer
        self._fb_file = open(FB_PATH, "r+b", buffering=0)
        self._fb_mm   = mmap.mmap(self._fb_file.fileno(), FB_SIZE)
        # Writable numpy view directly into the mmap — zero-copy flush
        self._fb_arr  = np.ndarray((FB_H, FB_W), dtype="<u2", buffer=self._fb_mm)

        self._closed = False
        atexit.register(self.close)

    # ------------------------------------------------------------------
    # Drawing primitives
    # ------------------------------------------------------------------

    def clear(self, rgb: tuple = BLACK) -> None:
        """Fill the entire buffer with a colour."""
        if rgb == self._bg:
            np.copyto(self._buf, self._clear_tpl)   # fast 1.5ms memcpy
        else:
            self._buf[:, :, 0] = rgb[0]
            self._buf[:, :, 1] = rgb[1]
            self._buf[:, :, 2] = rgb[2]

    def rect(self, x: int, y: int, w: int, h: int, rgb: tuple) -> None:
        """Draw a filled rectangle (pixel coords)."""
        x1 = min(x + w, FB_W);  x0 = max(x, 0)
        y1 = min(y + h, FB_H);  y0 = max(y, 0)
        if x1 > x0 and y1 > y0:
            self._buf[y0:y1, x0:x1] = rgb

    def line(self, x0: int, y0: int, x1: int, y1: int, rgb: tuple, width: int = 1) -> None:
        """Draw a line (thin wrapper — uses PIL for non-axis-aligned lines)."""
        # Axis-aligned fast paths
        if y0 == y1:
            self.rect(min(x0, x1), y0 - width // 2, abs(x1 - x0) + 1, width, rgb)
        elif x0 == x1:
            self.rect(x0 - width // 2, min(y0, y1), width, abs(y1 - y0) + 1, rgb)
        else:
            # Fall back to PIL for diagonal lines (rare)
            from PIL import ImageDraw
            tmp = Image.fromarray(self._buf, "RGB")
            ImageDraw.Draw(tmp).line((x0, y0, x1, y1), fill=rgb, width=width)
            self._buf[:] = np.asarray(tmp)

    def text(
        self,
        x: int,
        y: int,
        s: str,
        fg: tuple = GREEN_BRIGHT,
        bg: tuple | None = None,
    ) -> None:
        """Draw text at pixel coordinates using the PSF console font."""
        self._psf.draw_text_buf(self._buf, x, y, s, fg=fg, bg=bg)

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
        if 0 <= x < FB_W and 0 <= y < FB_H:
            self._buf[y, x] = rgb

    def image(self, x: int, y: int, img: Image.Image) -> None:
        """Composite a PIL Image at (x, y). Supports RGBA for alpha blending."""
        arr = np.asarray(img)
        h = min(arr.shape[0], FB_H - y)
        w = min(arr.shape[1], FB_W - x)
        if h <= 0 or w <= 0:
            return
        if img.mode == "RGBA":
            alpha = arr[:h, :w, 3:4].astype(np.float32) / 255.0
            rgb   = arr[:h, :w, :3].astype(np.float32)
            region = self._buf[y:y+h, x:x+w].astype(np.float32)
            self._buf[y:y+h, x:x+w] = (region * (1.0 - alpha) + rgb * alpha).astype(np.uint8)
        else:
            self._buf[y:y+h, x:x+w] = arr[:h, :w, :3]

    # ------------------------------------------------------------------
    # Flush to hardware
    # ------------------------------------------------------------------

    def flush(self) -> None:
        """Convert buffer to RGB565 and write directly into the mmap numpy view."""
        r = self._buf[:, :, 0].astype("<u2")
        g = self._buf[:, :, 1].astype("<u2")
        b = self._buf[:, :, 2].astype("<u2")
        self._fb_arr[:] = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Blank fb0 and release it."""
        if self._closed:
            return
        self._closed = True
        try:
            self._fb_mm[:] = b"\x00" * FB_SIZE
        except Exception:
            pass
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
