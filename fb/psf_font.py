"""fb/psf_font.py — PSF1/PSF2 bitmap font loader for PIL.

Loads the system console font (VGA 8×8) and renders it directly
into PIL Images at pixel-exact fidelity — the same rendering fbcon
uses, so the compositor output looks identical to the terminal.
"""

from __future__ import annotations
import gzip
import struct
from pathlib import Path

import numpy as np
from PIL import Image


PSF1_MAGIC = 0x0436
PSF2_MAGIC = 0x864AB572

# The font the console uses (FONTFACE=VGA FONTSIZE=8x8)
DEFAULT_PSF = "/usr/share/consolefonts/Lat2-VGA8.psf.gz"


class PSFFont:
    """Pixel-exact PSF1/PSF2 bitmap font renderer.

    Attributes:
        width, height  — glyph dimensions in pixels
    """

    def __init__(self, path: str = DEFAULT_PSF) -> None:
        data = self._read(path)
        self._glyphs: list[bytes] = []
        self._unicode_map: dict[int, int] = {}  # codepoint → glyph index
        self._img_cache: dict[tuple, Image.Image] = {}  # (idx, fg) → RGBA PIL Image
        self._glyph_arr: np.ndarray | None = None  # built lazily after load

        magic16 = struct.unpack_from("<H", data, 0)[0]
        magic32 = struct.unpack_from("<I", data, 0)[0]

        if magic16 == PSF1_MAGIC:
            self._load_psf1(data)
        elif magic32 == PSF2_MAGIC:
            self._load_psf2(data)
        else:
            raise ValueError(f"Unrecognised PSF magic: 0x{magic16:04x}")

    # ------------------------------------------------------------------
    # Loaders
    # ------------------------------------------------------------------

    def _read(self, path: str) -> bytes:
        p = Path(path)
        raw = p.read_bytes()
        if path.endswith(".gz"):
            raw = gzip.decompress(raw)
        return raw

    def _load_psf1(self, data: bytes) -> None:
        mode     = data[2]
        charsize = data[3]         # bytes per glyph = height (width is always 8)
        self.width  = 8
        self.height = charsize

        n_glyphs = 512 if (mode & 0x01) else 256
        glyph_end = 4 + n_glyphs * charsize
        raw = data[4:glyph_end]
        for i in range(n_glyphs):
            self._glyphs.append(raw[i * charsize:(i + 1) * charsize])

        has_unicode = bool(mode & 0x02)
        if has_unicode:
            self._parse_psf1_unicode(data[glyph_end:], n_glyphs)
        else:
            # No table: assume identity mapping for first 256
            for cp in range(n_glyphs):
                self._unicode_map[cp] = cp

    def _parse_psf1_unicode(self, table: bytes, n_glyphs: int) -> None:
        pos = 0
        for glyph_idx in range(n_glyphs):
            while pos + 1 < len(table):
                cp = struct.unpack_from("<H", table, pos)[0]
                pos += 2
                if cp == 0xFFFF:
                    break
                if cp != 0xFFFE:   # 0xFFFE = start-of-sequence marker
                    self._unicode_map[cp] = glyph_idx

    def _load_psf2(self, data: bytes) -> None:
        (magic, version, hdr_size, flags,
         n_glyphs, bytes_per_glyph,
         self.height, self.width) = struct.unpack_from("<IIIIIIII", data, 0)

        raw = data[hdr_size:hdr_size + n_glyphs * bytes_per_glyph]
        for i in range(n_glyphs):
            self._glyphs.append(raw[i * bytes_per_glyph:(i + 1) * bytes_per_glyph])

        has_unicode = bool(flags & 0x01)
        if has_unicode:
            self._parse_psf2_unicode(data[hdr_size + n_glyphs * bytes_per_glyph:], n_glyphs)
        else:
            for cp in range(n_glyphs):
                self._unicode_map[cp] = cp

    def _parse_psf2_unicode(self, table: bytes, n_glyphs: int) -> None:
        pos = 0
        glyph_idx = 0
        while pos < len(table) and glyph_idx < n_glyphs:
            b = table[pos]
            if b == 0xFF:
                glyph_idx += 1
                pos += 1
                continue
            # Decode UTF-8 codepoint
            if b < 0x80:
                cp = b; pos += 1
            elif b < 0xE0:
                cp = ((b & 0x1F) << 6) | (table[pos+1] & 0x3F); pos += 2
            elif b < 0xF0:
                cp = ((b & 0x0F) << 12) | ((table[pos+1] & 0x3F) << 6) | (table[pos+2] & 0x3F); pos += 3
            else:
                cp = ((b & 0x07) << 18) | ((table[pos+1] & 0x3F) << 12) | ((table[pos+2] & 0x3F) << 6) | (table[pos+3] & 0x3F); pos += 4
            if cp != 0xFFFE:
                self._unicode_map[cp] = glyph_idx

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _glyph_idx(self, char: str) -> int | None:
        cp = ord(char)
        idx = self._unicode_map.get(cp)
        if idx is None:
            idx = self._unicode_map.get(0x3F)   # '?' fallback
        if idx is None or idx >= len(self._glyphs):
            return None
        return idx

    def _glyph_image(self, idx: int, fg: tuple) -> Image.Image:
        """Return a cached RGBA PIL Image for glyph idx in colour fg.

        The image has full alpha (255) on foreground pixels and zero
        alpha on background pixels, so PIL paste() with mask=self uses
        it as a stamp — only foreground bits overwrite the destination.
        """
        key = (idx, fg)
        img = self._img_cache.get(key)
        if img is None:
            img = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
            px = img.load()
            fr, fg2, fb = fg
            for row, byte in enumerate(self._glyphs[idx][:self.height]):
                for col in range(self.width):
                    if (byte >> (7 - col)) & 1:
                        px[col, row] = (fr, fg2, fb, 255)
            self._img_cache[key] = img
        return img

    def draw_char(
        self,
        img: Image.Image,
        x: int,
        y: int,
        char: str,
        fg: tuple[int, int, int],
        bg: tuple[int, int, int] | None = None,
    ) -> None:
        """Draw a single character at pixel (x, y)."""
        if char == ' ' and bg is None:
            return   # transparent space — nothing to paint
        idx = self._glyph_idx(char)
        if idx is None:
            return
        if bg is not None:
            img.paste(bg, (x, y, x + self.width, y + self.height))
        glyph_img = self._glyph_image(idx, fg)
        img.paste(glyph_img, (x, y), mask=glyph_img)

    def draw_text(
        self,
        img: Image.Image,
        x: int,
        y: int,
        text: str,
        fg: tuple[int, int, int],
        bg: tuple[int, int, int] | None = None,
    ) -> None:
        """Draw a string of characters at pixel (x, y)."""
        cx = x
        for char in text:
            self.draw_char(img, cx, y, char, fg, bg)
            cx += self.width

    # ------------------------------------------------------------------
    # Numpy-native rendering (faster — no PIL paste overhead)
    # ------------------------------------------------------------------

    def _glyph_mask(self, idx: int) -> np.ndarray:
        """Return a cached (height, width) bool array: True = foreground pixel."""
        key = ('mask', idx)
        m = self._img_cache.get(key)
        if m is None:
            m = np.zeros((self.height, self.width), dtype=bool)
            for row, byte in enumerate(self._glyphs[idx][:self.height]):
                for col in range(self.width):
                    if (byte >> (7 - col)) & 1:
                        m[row, col] = True
            self._img_cache[key] = m
        return m

    def _ensure_glyph_arr(self) -> None:
        """Build self._glyph_arr: (n_glyphs, h, w) bool, indexed by glyph index."""
        if self._glyph_arr is not None:
            return
        n = len(self._glyphs)
        arr = np.zeros((n, self.height, self.width), dtype=bool)
        for idx in range(n):
            arr[idx] = self._glyph_mask(idx)
        self._glyph_arr = arr

    def draw_char_buf(
        self,
        buf: np.ndarray,
        x: int,
        y: int,
        char: str,
        fg: tuple[int, int, int],
        bg: tuple[int, int, int] | None = None,
    ) -> None:
        """Draw a single character into a (H, W, 3) uint8 numpy array."""
        if char == ' ' and bg is None:
            return
        idx = self._glyph_idx(char)
        if idx is None:
            return
        bh, bw = buf.shape[:2]
        if y >= bh or x >= bw or y + self.height <= 0 or x + self.width <= 0:
            return
        gy0 = max(0, -y);  gy1 = gy0 + min(self.height - gy0, bh - max(0, y))
        gx0 = max(0, -x);  gx1 = gx0 + min(self.width  - gx0, bw - max(0, x))
        region = buf[max(0, y):max(0, y) + (gy1 - gy0),
                     max(0, x):max(0, x) + (gx1 - gx0)]
        mask = self._glyph_mask(idx)[gy0:gy1, gx0:gx1]
        if bg is not None:
            region[~mask] = bg
        region[mask] = fg

    def draw_text_buf(
        self,
        buf: np.ndarray,
        x: int,
        y: int,
        text: str,
        fg: tuple[int, int, int],
        bg: tuple[int, int, int] | None = None,
    ) -> None:
        """Draw a string into a (H, W, 3) uint8 numpy array.

        Uses vectorised row rendering (single boolean-index over the whole
        text strip) which is ~5× faster than per-character PIL paste for
        typical line lengths.  Falls back to per-char for bg != None.
        """
        if not text:
            return
        bh, bw = buf.shape[:2]
        if y >= bh or y + self.height <= 0:
            return

        if bg is not None:
            # Background fill: use per-char path (rare in practice)
            cx = x
            for char in text:
                self.draw_char_buf(buf, cx, y, char, fg, bg)
                cx += self.width
            return

        # Transparent text — vectorised row approach
        self._ensure_glyph_arr()
        fb = self._unicode_map.get(0x3F, 0)
        # Resolve glyph indices (spaces → all-zero glyph → invisible, no extra cost)
        indices = [self._unicode_map.get(ord(c), fb) for c in text]

        n   = len(indices)
        cw  = self.width
        ch  = self.height

        # Clip x range
        x0  = max(0, x)
        x1  = min(x + n * cw, bw)
        if x1 <= x0:
            return
        y0  = max(0, y)
        y1  = min(y + ch, bh)

        # Glyph column slice within the clipped x window
        bx0 = x0 - x          # first pixel column inside the clipped region
        bx1 = bx0 + (x1 - x0)
        by0 = y0 - y
        by1 = by0 + (y1 - y0)

        # (n, ch, cw) → (ch, n*cw) bool mask for the full row strip
        row_mask = self._glyph_arr[indices].transpose(1, 0, 2).reshape(ch, n * cw)

        region = buf[y0:y1, x0:x1]            # (clip_h, clip_w, 3) view
        region[row_mask[by0:by1, bx0:bx1]] = fg

    # ------------------------------------------------------------------
    # Numpy-native rendering — RGB565 (uint16) buffers
    # ------------------------------------------------------------------

    def draw_char_buf16(
        self,
        buf: np.ndarray,
        x: int,
        y: int,
        char: str,
        fg: np.uint16,
        bg: np.uint16 | None = None,
    ) -> None:
        """Draw a single character into a (H, W) uint16 RGB565 array."""
        if char == ' ' and bg is None:
            return
        idx = self._glyph_idx(char)
        if idx is None:
            return
        bh, bw = buf.shape[:2]
        if y >= bh or x >= bw or y + self.height <= 0 or x + self.width <= 0:
            return
        gy0 = max(0, -y);  gy1 = gy0 + min(self.height - gy0, bh - max(0, y))
        gx0 = max(0, -x);  gx1 = gx0 + min(self.width  - gx0, bw - max(0, x))
        region = buf[max(0, y):max(0, y) + (gy1 - gy0),
                     max(0, x):max(0, x) + (gx1 - gx0)]
        mask = self._glyph_mask(idx)[gy0:gy1, gx0:gx1]
        if bg is not None:
            region[~mask] = bg
        region[mask] = fg

    def draw_text_buf16(
        self,
        buf: np.ndarray,
        x: int,
        y: int,
        text: str,
        fg: np.uint16,
        bg: np.uint16 | None = None,
    ) -> None:
        """Draw a string into a (H, W) uint16 RGB565 array.

        Same vectorised approach as draw_text_buf but assigns a scalar
        uint16 instead of an RGB tuple — works with the native RGB565
        compositor buffer.
        """
        if not text:
            return
        bh, bw = buf.shape[:2]
        if y >= bh or y + self.height <= 0:
            return

        if bg is not None:
            cx = x
            for char in text:
                self.draw_char_buf16(buf, cx, y, char, fg, bg)
                cx += self.width
            return

        # Transparent text — vectorised row approach
        self._ensure_glyph_arr()
        fb = self._unicode_map.get(0x3F, 0)
        indices = [self._unicode_map.get(ord(c), fb) for c in text]

        n   = len(indices)
        cw  = self.width
        ch  = self.height

        x0  = max(0, x)
        x1  = min(x + n * cw, bw)
        if x1 <= x0:
            return
        y0  = max(0, y)
        y1  = min(y + ch, bh)

        bx0 = x0 - x
        bx1 = bx0 + (x1 - x0)
        by0 = y0 - y
        by1 = by0 + (y1 - y0)

        row_mask = self._glyph_arr[indices].transpose(1, 0, 2).reshape(ch, n * cw)

        region = buf[y0:y1, x0:x1]            # (clip_h, clip_w) view
        region[row_mask[by0:by1, bx0:bx1]] = fg
