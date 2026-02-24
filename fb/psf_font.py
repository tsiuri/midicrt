"""fb/psf_font.py — PSF1/PSF2 bitmap font loader for PIL.

Loads the system console font (VGA 8×8) and renders it directly
into PIL Images at pixel-exact fidelity — the same rendering fbcon
uses, so the compositor output looks identical to the terminal.
"""

from __future__ import annotations
import gzip
import struct
from pathlib import Path

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

    def _glyph_for(self, char: str) -> bytes | None:
        cp = ord(char)
        idx = self._unicode_map.get(cp)
        if idx is None:
            idx = self._unicode_map.get(0x3F)   # '?' fallback
        if idx is None or idx >= len(self._glyphs):
            return None
        return self._glyphs[idx]

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
        glyph = self._glyph_for(char)
        if glyph is None:
            return
        px = img.load()
        w, h = img.size
        for row, byte in enumerate(glyph[:self.height]):
            py = y + row
            if py < 0 or py >= h:
                continue
            for col in range(self.width):
                px_x = x + col
                if px_x < 0 or px_x >= w:
                    continue
                bit = (byte >> (7 - col)) & 1
                if bit:
                    px[px_x, py] = fg
                elif bg is not None:
                    px[px_x, py] = bg

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
