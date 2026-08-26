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


# ---------------------------------------------------------------------------
# Waveform glyphs + selector strips
# ---------------------------------------------------------------------------

_NOISE_SEED = [0.1, 0.9, 0.3, 0.7, 0.5, 0.95, 0.2, 0.6, 0.85, 0.05, 0.4, 0.75,
               0.25, 0.65, 0.15, 0.8, 0.45, 0.35, 0.9, 0.55]


def wave_points(kind, n=32):
    """Normalised (t 0..1, v 0..1) samples of one cycle of a waveform kind."""
    import math
    pts = []
    k = kind.lower()
    for i in range(n + 1):
        t = i / n
        if k in ("sine",):
            v = 0.5 + 0.5 * math.sin(2 * math.pi * t)
        elif k in ("tri", "triangle"):
            v = 1 - abs((t * 2 + 0.5) % 2 - 1)
        elif k in ("saw", "sawdown", "saw down"):
            v = 1 - (t % 1.0)
        elif k in ("sawup", "saw up"):
            v = t % 1.0
        elif k in ("square",):
            v = 1.0 if (t % 1.0) < 0.5 else 0.0
        elif k in ("pulse",):
            v = 1.0 if (t % 1.0) < 0.25 else 0.0
        elif k in ("noise",):
            v = _NOISE_SEED[i % len(_NOISE_SEED)]
        elif k in ("random",):
            a, b = _NOISE_SEED[(i // 4) % 20], _NOISE_SEED[(i // 4 + 1) % 20]
            v = a + (b - a) * ((i % 4) / 4.0)
        elif k in ("sh", "s&h", "sample&hold", "sample & hold"):
            v = _NOISE_SEED[(i // 5) % 20]
        elif k in ("off",):
            v = 0.5
        else:   # unknown / reserved
            v = 0.5
        pts.append((t, v))
    return pts


def wave_kinds(label):
    """'Pulse+Saw+Noise' -> ['pulse', 'saw', 'noise']; unknown -> ['?']."""
    out = []
    for part in str(label).split("+"):
        p = part.strip().lower()
        if p in ("off",):
            out.append("off")
        elif p.startswith("pulse"):
            out.append("pulse")
        elif p in ("saw", "sawtooth", "saw down"):
            out.append("saw")
        elif p == "saw up":
            out.append("sawup")
        elif p in ("square", "sqr"):
            out.append("square")
        elif p in ("triangle", "tri"):
            out.append("tri")
        elif p in ("random",):
            out.append("random")
        elif p in ("noise",):
            out.append("noise")
        elif p in ("s&h", "sample&hold", "sample & hold"):
            out.append("sh")
        elif p in ("sine",):
            out.append("sine")
        else:
            out.append("?")
    return out or ["?"]


class _CanvasWaves:
    pass


def _wave_glyph(self, x, y, w, h, label, selected=False, focused=False):
    """One waveform icon. selected = backlit (filled cell, dark trace)."""
    cell_bg = (GREEN_BRIGHT if focused else GREEN_MID) if selected else self.bg
    trace = BLACK if selected else (GREEN_MID if focused else GREEN_DIM)
    self.rect(x, y, w, h, cell_bg)
    if not selected:
        self.outline(x, y, w, h, GREEN_GRID)
    kinds = wave_kinds(label)
    inner_x, inner_y, inner_w, inner_h = x + 2, y + 2, w - 4, h - 4
    if inner_w < 4 or inner_h < 3:
        return
    for kind in kinds:
        if kind == "off":
            self.rect(inner_x, inner_y + inner_h // 2, inner_w, 1, trace)
            continue
        if kind == "?":
            self.text(inner_x, inner_y, "?", fg=trace)
            continue
        pts = [(inner_x + int(t * inner_w), inner_y + int((1 - v) * (inner_h - 1)))
               for t, v in wave_points(kind, n=max(8, inner_w // 2))]
        self.polyline(pts, trace, thick=1 if h < 12 else 2)


def _wave_strip(self, x, y, w, h, labels, selected, focused=False, expand=1.6):
    """Row of waveform icons; the selected one is backlit and wider."""
    n = max(1, len(labels))
    unit = w / (n - 1 + expand) if n > 1 else w
    px = x
    for i, lab in enumerate(labels):
        cw_ = unit * (expand if i == selected else 1.0)
        self._wave_glyph(int(px), y, int(cw_) - 1, h, lab, selected=(i == selected), focused=focused)
        px += cw_


Canvas._wave_glyph = _wave_glyph
Canvas.wave_glyph = _wave_glyph
Canvas.wave_strip = _wave_strip
