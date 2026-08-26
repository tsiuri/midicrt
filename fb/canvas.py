"""fb/canvas.py — pixel drawing helper handed to CanvasWidget painters.

Wraps the fb Compositor's RGB565 numpy buffer with the primitives a
controller page needs (bars, lines, polylines, filled curves, text) in pixel
coordinates relative to the page content area (below the 3 header rows).
"""
from __future__ import annotations

import numpy as np

from fb.compositor import GREEN_BRIGHT, GREEN_MID, GREEN_DIM, BLACK, _rgb565

GREEN_FAINT = _rgb565(0, 80, 26)
GREEN_GRID = _rgb565(0, 40, 14)
AMBER = _rgb565(255, 190, 40)
AMBER_DIM = _rgb565(120, 90, 20)


class Canvas:
    def __init__(self, comp, y0_px: int):
        self.comp = comp
        self.buf = comp._buf
        self.H, self.W = self.buf.shape
        self.cw, self.ch = comp.char_w, comp.char_h
        self.y0 = y0_px
        self.bg = getattr(comp, "_bg565", BLACK)

    # --- coordinate helpers -------------------------------------------------
    def col_px(self, col: int) -> int:
        return col * self.cw

    def row_px(self, row: int) -> int:
        return self.y0 + row * self.ch

    # --- primitives -----------------------------------------------------------
    def rect(self, x, y, w, h, color):
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(self.W, int(x + w)), min(self.H, int(y + h))
        if x1 > x0 and y1 > y0:
            self.buf[y0:y1, x0:x1] = color

    def outline(self, x, y, w, h, color):
        self.rect(x, y, w, 1, color)
        self.rect(x, y + h - 1, w, 1, color)
        self.rect(x, y, 1, h, color)
        self.rect(x + w - 1, y, 1, h, color)

    def text(self, x, y, s, fg=GREEN_BRIGHT, bg=None):
        self.comp.text(int(x), int(y), s, fg=fg, bg=bg)

    def line(self, x0, y0, x1, y1, color, thick=1):
        n = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        xs = np.linspace(x0, x1, n).round().astype(int)
        ys = np.linspace(y0, y1, n).round().astype(int)
        for t in range(thick):
            yy = np.clip(ys + t, 0, self.H - 1)
            xx = np.clip(xs, 0, self.W - 1)
            self.buf[yy, xx] = color

    def polyline(self, pts, color, thick=1):
        for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
            self.line(xa, ya, xb, yb, color, thick)

    def fill_under(self, pts, base_y, color):
        """Fill the area between a polyline and a baseline (column-wise)."""
        for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
            n = int(abs(xb - xa)) + 1
            xs = np.linspace(xa, xb, n).round().astype(int)
            ys = np.linspace(ya, yb, n)
            for x, y in zip(xs, ys):
                if 0 <= x < self.W:
                    yt, yb_ = int(round(min(y, base_y))), int(round(max(y, base_y)))
                    self.buf[max(0, yt):min(self.H, yb_), x] = color

    # --- controller widgets ---------------------------------------------------
    def hbar(self, x, y, w, h, frac, bipolar=False, focused=False, unknown=False):
        """Horizontal gauge. frac 0..1 (bipolar: 0.5 = centre)."""
        border = GREEN_BRIGHT if focused else GREEN_DIM
        self.rect(x, y, w, h, self.bg)
        self.outline(x, y, w, h, border)
        if unknown:
            for xx in range(x + 3, x + w - 3, 6):
                self.rect(xx, y + h // 2, 2, 1, GREEN_DIM)
            return
        frac = max(0.0, min(1.0, float(frac)))
        inner_x, inner_w = x + 2, w - 4
        fill = GREEN_BRIGHT if focused else GREEN_MID
        if bipolar:
            mid = inner_x + inner_w // 2
            self.rect(mid, y + 2, 1, h - 4, GREEN_DIM)
            end = inner_x + int(round(frac * inner_w))
            if end >= mid:
                self.rect(mid, y + 2, max(1, end - mid), h - 4, fill)
            else:
                self.rect(end, y + 2, max(1, mid - end), h - 4, fill)
        else:
            self.rect(inner_x, y + 2, max(0, int(round(frac * inner_w))), h - 4, fill)

    def envelope(self, x, y, w, h, segments, peak=1.0, label=None, focused=False):
        """ADSR-style envelope plot. segments = [(width_weight, y_from, y_to)]
        with y in 0..1; draws grid, filled area, and the curve."""
        self.rect(x, y, w, h, self.bg)
        self.outline(x, y, w, h, GREEN_DIM)
        for gy in (0.25, 0.5, 0.75):
            self.rect(x + 1, y + int(h * gy), w - 2, 1, GREEN_GRID)
        total = sum(s[0] for s in segments) or 1.0
        px = x + 2
        pts = [(px, y + h - 2)]
        inner_w = w - 4
        inner_h = (h - 4) * 0.9
        for ww, ya, yb in segments:
            seg_w = ww / total * inner_w
            pts.append((px, y + h - 2 - int(round(ya * inner_h))))
            px += seg_w
            pts.append((int(round(px)), y + h - 2 - int(round(yb * inner_h))))
        pts.append((int(round(px)), y + h - 2))
        self.fill_under(pts, y + h - 2, GREEN_FAINT)
        self.polyline(pts, GREEN_BRIGHT if focused else GREEN_MID, thick=2)
        if label:
            self.text(x + 4, y + 2, label, fg=GREEN_DIM)
