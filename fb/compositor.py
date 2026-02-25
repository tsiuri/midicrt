"""fb/compositor.py — Full-screen framebuffer compositor.

Writes directly to /dev/fb0 (RGB565) using a native RGB565 numpy
uint16 render buffer.  All drawing operates in the hardware pixel
format — flush() is a simple memcpy with no per-pixel conversion.

No KD_GRAPHICS / vt-mode switching is used because on vc4-fkms-v3d
(Raspberry Pi Fake KMS) that call breaks x11vnc's view of the
framebuffer.

Caller is responsible for silencing the kernel console (fbcon) before
using this.

Requires: numpy

Typical usage:
    comp = Compositor()
    comp.clear()
    comp.text(0, 0, "Hello", fg=GREEN_BRIGHT_565)
    comp.rect(80, 40, 400, 8, comp.rgb565(0, 60, 20))
    comp.flush()
    ...
    comp.close()
"""

import atexit
import mmap

import numpy as np

from fb.psf_font import PSFFont

# --- Framebuffer constants ---
FB_PATH  = "/dev/fb0"
FB_W     = 800
FB_H     = 475
FB_SIZE  = FB_W * FB_H * 2   # RGB565: 2 bytes/pixel


def _rgb565(r: int, g: int, b: int) -> np.uint16:
    """Convert 8-bit RGB to a single RGB565 uint16 value."""
    return np.uint16(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3))


# --- CRT green palette (RGB565) ---
GREEN_BRIGHT = _rgb565(0, 255, 80)
GREEN_MID    = _rgb565(0, 180, 50)
GREEN_DIM    = _rgb565(0, 100, 30)
BLACK        = _rgb565(0, 0, 0)

# Legacy RGB888 tuples for callers that still need them
GREEN_BRIGHT_RGB = (0, 255, 80)
GREEN_MID_RGB    = (0, 180, 50)
GREEN_DIM_RGB    = (0, 100, 30)
BLACK_RGB        = (0, 0, 0)


class Compositor:
    """Pixel-level compositor backed by /dev/fb0.

    Uses a numpy (H, W) uint16 array as the primary render buffer in
    native RGB565 format.  All drawing writes RGB565 values directly.
    flush() is a simple memcpy into the mmap'd framebuffer — no
    per-pixel conversion needed.
    """

    def __init__(self, bg: np.uint16 = BLACK) -> None:
        # Load the console bitmap font (VGA 8×8)
        self._psf    = PSFFont()
        self.char_w  = self._psf.width
        self.char_h  = self._psf.height
        self.cols    = FB_W // self.char_w
        self.rows    = FB_H // self.char_h

        # Primary render buffer — native RGB565
        self._bg565  = bg
        self._buf    = np.full((FB_H, FB_W), bg, dtype=np.uint16)

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

    def clear(self, color: np.uint16 | None = None) -> None:
        """Fill the entire buffer with a colour (RGB565 uint16)."""
        self._buf[:] = self._bg565 if color is None else color

    def rect(self, x: int, y: int, w: int, h: int, color: np.uint16) -> None:
        """Draw a filled rectangle (pixel coords, RGB565)."""
        x1 = min(x + w, FB_W);  x0 = max(x, 0)
        y1 = min(y + h, FB_H);  y0 = max(y, 0)
        if x1 > x0 and y1 > y0:
            self._buf[y0:y1, x0:x1] = color

    def text(
        self,
        x: int,
        y: int,
        s: str,
        fg: np.uint16 = GREEN_BRIGHT,
        bg: np.uint16 | None = None,
    ) -> None:
        """Draw text at pixel coordinates using the PSF console font."""
        self._psf.draw_text_buf16(self._buf, x, y, s, fg=fg, bg=bg)

    # ------------------------------------------------------------------
    # Flush to hardware
    # ------------------------------------------------------------------

    def flush(self) -> None:
        """Copy the RGB565 back-buffer to /dev/fb0 (pure memcpy)."""
        np.copyto(self._fb_arr, self._buf)

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
    def rgb565(r: int, g: int, b: int) -> np.uint16:
        """Convert 8-bit RGB to RGB565."""
        return _rgb565(r, g, b)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
