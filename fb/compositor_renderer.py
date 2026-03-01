"""fb/compositor_renderer.py — Full-system compositor renderer for midicrt.

Routes all midicrt rendering through fb/compositor (PIL → /dev/fb0)
instead of the terminal.  All pages using build_widget() get pixel
rendering automatically.  Pages using the legacy draw() path still
work but continue writing to the terminal (harmless while nothing
else is printing to tty1).

No KD_GRAPHICS / vt-mode: proved to break x11vnc on vc4-fkms-v3d.

Usage in midicrt:
    configure_startup_profile("run_compositor")
"""

from __future__ import annotations
import math
import time

import numpy as np
from configutil import load_section

from fb.compositor import (
    Compositor, GREEN_BRIGHT, GREEN_MID, GREEN_DIM, _rgb565,
)
from ui.model import (
    Column,
    EventLogWidget,
    FooterStatusWidget,
    Frame,
    NotesWidget,
    PianoRollWidget,
    Spacer,
    TextBlock,
    TransportWidget,
    Widget,
)
from ui.renderers.text import TextRenderer

BG = _rgb565(0, 8, 2)   # very dark green background

# PAGE_Y_OFFSET: rows 0-2 are header / transport / blank in midicrt
PAGE_Y_OFFSET = 3

# Per-channel note-bar colours (16 MIDI channels)
_CH_BASE_RGB = [(0, 255, 80)] * 16
_CH_COLOURS = [_rgb565(*rgb) for rgb in _CH_BASE_RGB]

# Notes badge palette (RGB565, module-level to avoid per-frame conversion cost)
_BADGE_BG = _rgb565(0, 30, 10)
_BADGE_BORDER = GREEN_MID

_SPEC_BG = _rgb565(0, 20, 8)
_SPEC_BAR_HI = _rgb565(0, 255, 80)
_SPEC_BAR_MID = _rgb565(0, 180, 60)
_SPEC_BAR_LO = _rgb565(0, 120, 40)
_SPEC_BORDER = _rgb565(0, 180, 50)

_PIANO_BG = _rgb565(0, 20, 8)
_PIANO_BORDER = _rgb565(0, 120, 40)
_PIANO_WHITE_OFF = _rgb565(0, 55, 20)
_PIANO_WHITE_ON = _rgb565(0, 210, 70)
_PIANO_WHITE_TOP = _rgb565(0, 95, 35)
_PIANO_WHITE_SHADOW = _rgb565(0, 28, 10)
_PIANO_BLACK_OFF = _rgb565(0, 8, 3)
_PIANO_BLACK_ON = _rgb565(0, 150, 50)
_PIANO_BLACK_TOP = _rgb565(0, 50, 18)
_PIANO_BLACK_SHADOW = _rgb565(0, 4, 1)

_MINI_BG = _rgb565(0, 14, 6)
_MINI_BORDER = _rgb565(0, 120, 40)
_MINI_NOTE_BASE_RGB = (0, 255, 80)
_MINI_NOTE = _rgb565(0, 128, 40)
_MINI_NOTE_HI = _rgb565(*_MINI_NOTE_BASE_RGB)
_MINI_GUIDE = _rgb565(0, 60, 20)
_MINI_FLARE_CORE = _rgb565(0, 255, 80)
_MINI_FLARE_GLOW = _rgb565(0, 140, 45)
_BADGE_UPDATE_HZ = 24.0
_VEL_BRIGHTNESS_FLOOR = 0.5

# Piano-roll guide lines (all monochrome-green friendly).
_ROLL_H_DOT = _rgb565(0, 38, 15)        # faint between-note dotted rows
_ROLL_H_DOT_C = _rgb565(0, 68, 27)      # slightly brighter on C rows
_ROLL_V_BAR_DOT = _rgb565(0, 86, 33)    # dotted bar-tracking verticals
_ROLL_ACTIVE_ROW_BASE_RGB = (0, 38, 12)  # 15% row tint at full intensity
_ROLL_ACTIVE_ROW_FADE_S = 1.0
_ROLL_ACTIVE_ROW_FADE_STEPS = 64
_CC_LANE_BG = _rgb565(0, 24, 10)
_CC_LANE_BAR = _rgb565(0, 120, 44)
_CC_LANE_BAR_HI = _rgb565(0, 185, 64)

_PIANOROLL_PERF_DEFAULTS = {
    "enabled": True,
    "frame_budget_ms": 16.67,
    "ema_alpha": 0.12,
    "hysteresis_up_frames": 5,
    "hysteresis_down_frames": 24,
    "tier1": {"frame_ms": 15.5, "miss_ratio": 0.12},
    "tier2": {"frame_ms": 18.5, "miss_ratio": 0.22},
    "tier3": {"frame_ms": 24.0, "miss_ratio": 0.40},
    "effects": {
        "overlap_flash": True,
        "row_fade": True,
        "dotted_guides": True,
    },
}


class CompositorRenderer(TextRenderer):
    """Routes all midicrt rendering through fb/compositor (PIL → fb0).

    midicrt's ui_loop calls:
        renderer.frame_clear()      — at the start of each frame
        draw_line(row, text)        — for header / transport rows (0-2)
        renderer.render(widget, frame) — for page content (rows 3+)
        renderer.frame_flush()      — at the end of each frame

    Overlays (screensaver blanking, bouncing shapes, HUD) can be drawn
    directly on self.comp between render() and frame_flush().
    """

    def __init__(self) -> None:
        super().__init__()
        self.comp = Compositor(bg=BG)
        self._badge_frames = None  # lazily pre-computed bar animation
        self._badge_cache = None
        self._badge_cache_rect = None  # (x, y, w, h)
        self._badge_last_update_t = 0.0
        self._badge_update_interval = 1.0 / max(1.0, float(_BADGE_UPDATE_HZ))
        # Pre-compute velocity-scaled RGB565 colours for piano roll cells.
        self._vel_lut = []
        for base_rgb in _CH_BASE_RGB:
            lut = np.zeros(128, dtype=np.uint16)
            for v in range(128):
                scale = self._velocity_scale(v)
                r, g, b = (int(c * scale) for c in base_rgb)
                lut[v] = _rgb565(r, g, b)
            self._vel_lut.append(lut)
        # Active-row tint fade state (pitch -> fade-end time).
        self._roll_row_fade_until: dict[int, float] = {}
        # Quantized fade LUT for cheap per-row tint decay.
        self._roll_row_fade_lut = np.zeros(_ROLL_ACTIVE_ROW_FADE_STEPS + 1, dtype=np.uint16)
        rr, rg, rb = _ROLL_ACTIVE_ROW_BASE_RGB
        for i in range(_ROLL_ACTIVE_ROW_FADE_STEPS + 1):
            s = float(i) / float(_ROLL_ACTIVE_ROW_FADE_STEPS)
            self._roll_row_fade_lut[i] = _rgb565(int(rr * s), int(rg * s), int(rb * s))
        self._pr_perf_cfg = self._load_pianoroll_perf_config()
        self._pr_perf_tier = 0
        self._pr_ema_ms = 0.0
        self._pr_miss_ratio = 0.0
        self._pr_tier_up_count = 0
        self._pr_tier_down_count = 0

    def _load_pianoroll_perf_config(self) -> dict:
        cfg = load_section("pianoroll_perf") or {}
        out = dict(_PIANOROLL_PERF_DEFAULTS)
        out.update({k: v for k, v in cfg.items() if k not in {"tier1", "tier2", "tier3", "effects"}})
        for tier in ("tier1", "tier2", "tier3"):
            tier_cfg = dict(_PIANOROLL_PERF_DEFAULTS[tier])
            loaded = cfg.get(tier)
            if isinstance(loaded, dict):
                tier_cfg.update(loaded)
            out[tier] = tier_cfg
        effects = dict(_PIANOROLL_PERF_DEFAULTS["effects"])
        loaded_effects = cfg.get("effects")
        if isinstance(loaded_effects, dict):
            effects.update(loaded_effects)
        out["effects"] = effects
        return out

    def _update_pianoroll_perf_tier(self, frame_ms: float) -> None:
        cfg = self._pr_perf_cfg
        if not bool(cfg.get("enabled", True)):
            self._pr_perf_tier = 0
            self._pr_ema_ms = frame_ms
            self._pr_miss_ratio = 0.0
            self._pr_tier_up_count = 0
            self._pr_tier_down_count = 0
            return
        alpha = max(0.01, min(1.0, float(cfg.get("ema_alpha", 0.12))))
        budget_ms = max(1.0, float(cfg.get("frame_budget_ms", 16.67)))
        miss = 1.0 if frame_ms > budget_ms else 0.0
        if self._pr_ema_ms <= 0.0:
            self._pr_ema_ms = frame_ms
        else:
            self._pr_ema_ms = (1.0 - alpha) * self._pr_ema_ms + alpha * frame_ms
        self._pr_miss_ratio = (1.0 - alpha) * self._pr_miss_ratio + alpha * miss

        desired = 0
        for idx, tier in enumerate(("tier1", "tier2", "tier3"), start=1):
            tc = cfg.get(tier, {})
            if self._pr_ema_ms >= float(tc.get("frame_ms", 1e9)) or self._pr_miss_ratio >= float(tc.get("miss_ratio", 1e9)):
                desired = idx

        up_frames = max(1, int(cfg.get("hysteresis_up_frames", 5)))
        down_frames = max(1, int(cfg.get("hysteresis_down_frames", 24)))
        if desired > self._pr_perf_tier:
            self._pr_tier_up_count += 1
            self._pr_tier_down_count = 0
            if self._pr_tier_up_count >= up_frames:
                self._pr_perf_tier = min(3, self._pr_perf_tier + 1)
                self._pr_tier_up_count = 0
        elif desired < self._pr_perf_tier:
            self._pr_tier_down_count += 1
            self._pr_tier_up_count = 0
            if self._pr_tier_down_count >= down_frames:
                self._pr_perf_tier = max(0, self._pr_perf_tier - 1)
                self._pr_tier_down_count = 0
        else:
            self._pr_tier_up_count = 0
            self._pr_tier_down_count = 0

    @staticmethod
    def _velocity_scale(velocity: int) -> float:
        """Map velocity 1..127 -> brightness 50%..100% (0 stays off)."""
        v = int(velocity)
        if v <= 0:
            return 0.0
        v = min(127, v)
        return _VEL_BRIGHTNESS_FLOOR + (1.0 - _VEL_BRIGHTNESS_FLOOR) * (v / 127.0)

    def _velocity_color565(self, base_rgb: tuple[int, int, int], velocity: int) -> int:
        scale = self._velocity_scale(velocity)
        if scale <= 0.0:
            return _rgb565(0, 0, 0)
        r, g, b = base_rgb
        return _rgb565(int(r * scale), int(g * scale), int(b * scale))

    # ------------------------------------------------------------------
    # Frame lifecycle
    # ------------------------------------------------------------------

    def frame_clear(self) -> None:
        """Fill the PIL buffer with the background colour."""
        self.comp.clear()

    def frame_flush(self) -> None:
        """Convert PIL buffer → RGB565 and write to /dev/fb0."""
        self.comp.flush()

    def _build_badge_frames(self) -> None:
        """Pre-compute 48 frames (2s loop) of the scrolling bar animation."""
        cw, ch = self.comp.char_w, self.comp.char_h
        label = "welcome to the jungle ^_^"
        bw = (len(label) + 2) * cw
        anim_h = 5 * ch
        max_bar = anim_h - 2
        xi_f = np.arange(1, bw - 1, dtype=np.float32)
        rows = np.arange(anim_h)[:, np.newaxis]
        bg = _rgb565(0, 20, 8)
        bar_hi = _rgb565(0, 255, 80)
        bar_mid = _rgb565(0, 180, 60)
        bar_lo = _rgb565(0, 120, 40)
        border = _rgb565(0, 180, 50)

        frames = []
        n_frames = 48  # 2 seconds at 24fps
        for fi in range(n_frames):
            t = fi * (2.0 * np.pi / 5.0) / n_frames  # one sine period
            region = np.full((anim_h, bw), bg, dtype=np.uint16)
            phase = np.float32(t * 5.0) + xi_f * np.float32(0.22)
            h_arr = np.clip(
                np.abs(np.sin(phase))           * np.float32(0.50) +
                np.abs(np.sin(phase * np.float32(2.3) + np.float32(1.1))) * np.float32(0.30) +
                np.abs(np.sin(phase * np.float32(0.7) + np.float32(2.5))) * np.float32(0.20),
                np.float32(1.0 / max_bar), np.float32(1.0)
            )
            h_arr = np.maximum(1, (h_arr * max_bar).astype(np.int32))
            top_arr = (1 + max_bar - h_arr)
            mid_arr = top_arr + np.maximum(1, h_arr // 2)
            tops  = top_arr[np.newaxis, :]
            mids  = mid_arr[np.newaxis, :]
            ends  = (top_arr + h_arr)[np.newaxis, :]
            inner = region[:, 1:-1]
            inner[rows == tops]                         = bar_hi
            inner[(rows > tops)  & (rows < mids)]       = bar_mid
            inner[(rows >= mids) & (rows < ends)]       = bar_lo
            region[0,  :]  = border
            region[-1, :]  = border
            region[:,  0]  = border
            region[:, -1]  = border
            frames.append(region)
        self._badge_frames = frames
        self._badge_idx = 0

    def _draw_badge_spectrum(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        levels: list[float] | None,
    ) -> None:
        """Draw a compact RGB565 spectrum panel for the notes badge."""
        comp = self.comp
        region = np.full((h, w), _SPEC_BG, dtype=np.uint16)
        inner_h = max(1, h - 2)
        inner_w = max(1, w - 2)

        vals = np.asarray(levels if levels else [], dtype=np.float32)
        if vals.size > 0:
            vals = np.clip(vals, 0.0, 1.0)
            if vals.size != inner_w:
                src_x = np.arange(vals.size, dtype=np.float32)
                dst_x = np.linspace(0, max(0, vals.size - 1), inner_w, dtype=np.float32)
                vals = np.interp(dst_x, src_x, vals).astype(np.float32)
            heights = np.clip((vals * inner_h + 0.5).astype(np.int32), 0, inner_h)
            inner = region[1:-1, 1:-1]
            for xi, bar_h in enumerate(heights):
                if bar_h <= 0:
                    continue
                y0 = inner_h - bar_h
                y_mid = y0 + max(1, bar_h // 2)
                inner[y0:inner_h, xi] = _SPEC_BAR_LO
                inner[y0:y_mid, xi] = _SPEC_BAR_MID
                inner[y0, xi] = _SPEC_BAR_HI

        region[0, :] = _SPEC_BORDER
        region[-1, :] = _SPEC_BORDER
        region[:, 0] = _SPEC_BORDER
        region[:, -1] = _SPEC_BORDER
        comp._buf[y:y + h, x:x + w] = region

    def _draw_badge_piano(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        active_pcs: set[int] | None,
    ) -> None:
        """Draw a 12-note octave piano (white + recessed black keys)."""
        comp = self.comp
        if w < 16 or h < 8:
            return
        pcs = {int(pc) % 12 for pc in (active_pcs or set())}
        region = np.full((h, w), _PIANO_BG, dtype=np.uint16)
        region[0, :] = _PIANO_BORDER
        region[-1, :] = _PIANO_BORDER
        region[:, 0] = _PIANO_BORDER
        region[:, -1] = _PIANO_BORDER

        ix0 = 1
        iy0 = 1
        iw = max(1, w - 2)
        ih = max(1, h - 2)

        white_pcs = [0, 2, 4, 5, 7, 9, 11]
        edges = np.linspace(0, iw, 8, dtype=np.int32)
        for idx, pc in enumerate(white_pcs):
            kx0 = int(edges[idx])
            kx1 = int(edges[idx + 1])
            kw = max(1, kx1 - kx0)
            fill = _PIANO_WHITE_ON if pc in pcs else _PIANO_WHITE_OFF
            x0 = ix0 + kx0
            x1 = min(w - 1, x0 + kw)
            region[iy0:iy0 + ih, x0:x1] = fill
            # Subtle recessed contour.
            region[iy0, x0:x1] = _PIANO_WHITE_TOP
            region[iy0:iy0 + ih, x0] = _PIANO_WHITE_TOP
            region[iy0:iy0 + ih, x1 - 1] = _PIANO_WHITE_SHADOW
            region[iy0 + ih - 1, x0:x1] = _PIANO_WHITE_SHADOW

        black_keys = [(1, 0), (3, 1), (6, 3), (8, 4), (10, 5)]
        bh = max(3, (ih * 2) // 3)
        for pc, left_idx in black_keys:
            boundary = int(edges[left_idx + 1])
            lw = int(edges[left_idx + 1] - edges[left_idx])
            rw = int(edges[left_idx + 2] - edges[left_idx + 1])
            bw = max(2, min(lw, rw) * 2 // 3)
            bx0 = boundary - (bw // 2)
            bx0 = max(0, min(iw - bw, bx0))
            fill = _PIANO_BLACK_ON if pc in pcs else _PIANO_BLACK_OFF
            x0 = ix0 + bx0
            x1 = min(w - 1, x0 + bw)
            region[iy0:iy0 + bh, x0:x1] = fill
            # Recessed top/edge
            region[iy0, x0:x1] = _PIANO_BLACK_TOP
            region[iy0:iy0 + bh, x1 - 1] = _PIANO_BLACK_SHADOW
            region[iy0 + bh - 1, x0:x1] = _PIANO_BLACK_SHADOW

        comp._buf[y:y + h, x:x + w] = region

    def _draw_badge_mini_roll(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        roll_payload: dict | None,
    ) -> None:
        """Draw a compact piano-roll overview panel."""
        comp = self.comp
        if w < 8 or h < 6:
            return
        region = np.full((h, w), _MINI_BG, dtype=np.uint16)
        region[0, :] = _MINI_BORDER
        region[-1, :] = _MINI_BORDER
        region[:, 0] = _MINI_BORDER
        region[:, -1] = _MINI_BORDER
        inner = region[1:-1, 1:-1]
        ih, iw = inner.shape
        inner[:, iw - 1] = _MINI_GUIDE

        payload = roll_payload if isinstance(roll_payload, dict) else {}
        pitch_low = int(payload.get("pitch_low", 36))
        pitch_high = int(payload.get("pitch_high", 84))
        if pitch_high <= pitch_low:
            pitch_high = pitch_low + 1
        pitch_span = float(pitch_high - pitch_low)

        roll_cols = max(1, int(payload.get("time_cols", 64)))
        tpc = max(1, int(payload.get("ticks_per_col", 6)))
        tick_now = int(payload.get("tick_now", payload.get("tick_right", 0)))
        tick_left = tick_now - max(1, roll_cols - 1) * tpc
        tick_right = tick_now + tpc
        tick_span = max(1.0, float(tick_right - tick_left))
        x_scale = (iw - 1) / tick_span
        y_scale = (ih - 1) / pitch_span

        def _x_at(tick: float) -> int:
            x_rel = int((float(tick) - float(tick_left)) * x_scale)
            return 0 if x_rel < 0 else (iw - 1 if x_rel >= iw else x_rel)

        def _y_at(pitch: int) -> int:
            y_rel = int((float(pitch_high) - float(pitch)) * y_scale)
            return 0 if y_rel < 0 else (ih - 1 if y_rel >= ih else y_rel)

        top_track = [0.0] * iw
        bot_track = [0.0] * iw

        spans = payload.get("spans", [])
        if isinstance(spans, list) and spans:
            for item in spans:
                if not isinstance(item, (list, tuple)) or len(item) < 5:
                    continue
                start_tick, end_tick, pitch, _ch, vel = item[:5]
                try:
                    pitch_i = int(pitch)
                    vel_i = int(vel)
                except Exception:
                    continue
                if vel_i <= 0 or pitch_i < pitch_low or pitch_i > pitch_high:
                    continue
                s = max(float(start_tick), float(tick_left))
                e = min(float(end_tick), float(tick_right))
                if e <= s:
                    continue
                x0 = _x_at(s)
                x1 = _x_at(e)
                if x1 < x0:
                    x0, x1 = x1, x0
                y0 = _y_at(pitch_i)
                inner[y0, x0:x1 + 1] = self._velocity_color565(_MINI_NOTE_BASE_RGB, vel_i)
        else:
            cols = payload.get("columns", [])
            if isinstance(cols, list) and cols:
                ncols = max(1, len(cols))
                x_denom = max(1, ncols - 1)
                for i, events in enumerate(cols):
                    if not isinstance(events, list):
                        continue
                    cx = int(i * (iw - 1) / x_denom)
                    for ev in events:
                        if not isinstance(ev, (list, tuple)) or len(ev) < 3:
                            continue
                        pitch, _ch, vel = ev[:3]
                        try:
                            pitch_i = int(pitch)
                            vel_i = int(vel)
                        except Exception:
                            continue
                        if vel_i <= 0 or pitch_i < pitch_low or pitch_i > pitch_high:
                            continue
                        y0 = _y_at(pitch_i)
                        inner[y0, cx] = self._velocity_color565(_MINI_NOTE_BASE_RGB, vel_i)

        cols = payload.get("columns", [])
        if isinstance(cols, list) and cols:
            ncols = max(1, len(cols))
            x_denom = max(1, ncols - 1)
            for i, events in enumerate(cols):
                if not isinstance(events, list):
                    continue
                tx = int(i * (iw - 1) / x_denom)
                for ev in events:
                    if not isinstance(ev, (list, tuple)) or len(ev) < 3:
                        continue
                    pitch, _ch, vel = ev[:3]
                    try:
                        pitch_i = int(pitch)
                        vel_i = max(0, int(vel))
                    except Exception:
                        continue
                    if vel_i <= 0:
                        continue
                    amp = float(min(127, vel_i)) / 127.0
                    if pitch_i > pitch_high:
                        top_track[tx] = max(top_track[tx], amp)
                    elif pitch_i < pitch_low:
                        bot_track[tx] = max(bot_track[tx], amp)

        overflow = payload.get("overflow", {})
        if isinstance(overflow, dict):
            if overflow.get("above") and not any(v > 0.0 for v in top_track):
                top_track[-1] = 1.0
            if overflow.get("below") and not any(v > 0.0 for v in bot_track):
                bot_track[-1] = 1.0

        flare_h = max(4, ih // 2)  # intentionally tall flare

        def _draw_track(track: list[float], top_side: bool) -> None:
            for tx, amp in enumerate(track):
                if amp <= 0.0:
                    continue
                fh = max(2, int(round(amp * flare_h)))
                if top_side:
                    y1 = min(ih, fh)
                    inner[:y1, tx] = _MINI_FLARE_GLOW
                    inner[0, tx] = _MINI_FLARE_CORE
                    if tx > 0:
                        inner[:max(1, y1 // 2), tx - 1] = _MINI_FLARE_GLOW
                    if tx + 1 < iw:
                        inner[:max(1, y1 // 2), tx + 1] = _MINI_FLARE_GLOW
                else:
                    y0 = max(0, ih - fh)
                    inner[y0:, tx] = _MINI_FLARE_GLOW
                    inner[ih - 1, tx] = _MINI_FLARE_CORE
                    if tx > 0:
                        inner[ih - max(1, (ih - y0) // 2):, tx - 1] = _MINI_FLARE_GLOW
                    if tx + 1 < iw:
                        inner[ih - max(1, (ih - y0) // 2):, tx + 1] = _MINI_FLARE_GLOW

        _draw_track(top_track, top_side=True)
        _draw_track(bot_track, top_side=False)
        comp._buf[y:y + h, x:x + w] = region

    def draw_notes_badge(
        self,
        spectrum_levels: list[float] | None = None,
        active_pcs: set[int] | None = None,
        roll_payload: dict | None = None,
    ) -> None:
        """Mini-roll + spectrum + test piano graphic + title badge."""
        comp = self.comp
        cw, ch = comp.char_w, comp.char_h
        label = "welcome to the jungle ^_^"
        bw = (len(label) + 2) * cw
        bh = 4 * ch
        spec_h = 5 * ch
        roll_h = 6 * ch
        x = 800 - bw - 4
        badge_y = 475 - bh - 4
        spec_y = badge_y - spec_h
        roll_y = spec_y - roll_h
        total_h = roll_h + spec_h + bh
        cache_rect = (x, roll_y, bw, total_h)
        now_t = time.monotonic()

        if (
            self._badge_cache is not None
            and self._badge_cache_rect == cache_rect
            and (now_t - self._badge_last_update_t) < self._badge_update_interval
        ):
            comp._buf[roll_y:roll_y + total_h, x:x + bw] = self._badge_cache
            return

        # --- Mini piano-roll panel ---
        self._draw_badge_mini_roll(x, roll_y, bw, roll_h, roll_payload)

        # --- Spectrum panel (fallback to old animation if no levels yet) ---
        if spectrum_levels:
            self._draw_badge_spectrum(x, spec_y, bw, spec_h, spectrum_levels)
        else:
            if self._badge_frames is None:
                self._build_badge_frames()
            comp._buf[spec_y:spec_y + spec_h, x:x + bw] = self._badge_frames[self._badge_idx]
            self._badge_idx = (self._badge_idx + 1) % len(self._badge_frames)

        # --- Badge box ---
        comp.rect(x, badge_y, bw, bh, _BADGE_BG)
        comp.rect(x, badge_y, bw, 1, _BADGE_BORDER)
        comp.rect(x, badge_y + bh - 1, bw, 1, _BADGE_BORDER)
        comp.rect(x, badge_y, 1, bh, _BADGE_BORDER)
        comp.rect(x + bw - 1, badge_y, 1, bh, _BADGE_BORDER)
        self._draw_badge_piano(x + 1, badge_y + 1, bw - 2, max(8, (2 * ch) - 1), active_pcs)
        comp.text(x + cw, badge_y + (2 * ch) + 1, label, fg=GREEN_BRIGHT)
        self._badge_cache = comp._buf[roll_y:roll_y + total_h, x:x + bw].copy()
        self._badge_cache_rect = cache_rect
        self._badge_last_update_t = now_t

    # ------------------------------------------------------------------
    # Line-level drawing (header / transport rows, drawn before render())
    # ------------------------------------------------------------------

    def draw_text_line(self, row: int, text: str) -> None:
        """Render a plain text line at character-row 'row'."""
        # Fast path: skip regex for text with no ANSI escape sequences
        if '\x1b' in text:
            plain = self.term.strip_seqs(text).rstrip()
        else:
            plain = text.rstrip()
        if plain:
            self.comp.text(0, row * self.comp.char_h, plain, fg=GREEN_BRIGHT)

    # ------------------------------------------------------------------
    # Renderer protocol
    # ------------------------------------------------------------------

    def render(self, widget: Widget, frame: Frame) -> list[str]:
        """Draw the widget tree into the compositor buffer.

        Returns a list of empty strings so that midicrt's subsequent
        draw_line(3+idx, line) calls are no-ops (the compositor has
        already drawn everything).
        """
        self._render_widget(widget, frame, y_row=0)
        return [""] * frame.rows


    @staticmethod
    def _line_plain(text: str):
        from ui.model import Line

        return Line.plain(text)

    # ------------------------------------------------------------------
    # Widget rendering
    # ------------------------------------------------------------------

    @staticmethod
    def _line_to_plain(line, cols: int) -> str:
        """Fast plain-text extraction for compositor path (no ANSI styling)."""
        if cols <= 0:
            return ""
        out = []
        rem = cols
        for seg in getattr(line, "segments", ()):
            txt = getattr(seg, "text", "")
            if not txt:
                continue
            if len(txt) >= rem:
                out.append(txt[:rem])
                rem = 0
                break
            out.append(txt)
            rem -= len(txt)
        if rem > 0:
            out.append(" " * rem)
        return "".join(out)

    def _render_widget(self, widget: Widget, frame: Frame, y_row: int) -> int:
        """Recursively render a widget into the compositor buffer.

        y_row is in content-area row coordinates (0 = first row below
        the header block, i.e. pixel row PAGE_Y_OFFSET * char_h).
        Returns the next free y_row.
        """
        if isinstance(widget, TransportWidget):
            block = TextBlock(lines=[
                self._line_plain(f"Running: {widget.running}"),
                self._line_plain(f"Bar Counter: {widget.bar}"),
                self._line_plain(f"BPM: {widget.bpm:5.1f}"),
                self._line_plain(f"Ticks: {widget.tick}"),
                self._line_plain(widget.time_signature or "Time Signature: (no lock)"),
            ])
            return self._render_widget(block, frame, y_row)

        if isinstance(widget, NotesWidget):
            return self._render_widget(TextBlock(lines=list(widget.lines)), frame, y_row)

        if isinstance(widget, EventLogWidget):
            lines = [self._line_plain(widget.title), self._line_plain(widget.filter_summary)]
            lines.extend(self._line_plain(e) for e in widget.entries)
            if widget.marker:
                lines.append(self._line_plain(widget.marker))
            return self._render_widget(TextBlock(lines=lines), frame, y_row)

        if isinstance(widget, FooterStatusWidget):
            txt = f"{widget.left} {widget.right}".strip() if widget.right else widget.left
            return self._render_widget(TextBlock(lines=[self._line_plain(txt)]), frame, y_row)

        if isinstance(widget, TextBlock):
            for line in widget.lines:
                if y_row >= frame.rows:
                    break
                plain = self._line_to_plain(line, frame.cols).rstrip()
                if plain:
                    px_y = (PAGE_Y_OFFSET + y_row) * self.comp.char_h
                    self.comp.text(0, px_y, plain, fg=GREEN_BRIGHT)
                y_row += 1
            return y_row

        if isinstance(widget, Spacer):
            return y_row + max(0, widget.rows)

        if isinstance(widget, Column):
            for child in widget.children:
                y_row = self._render_widget(child, frame, y_row)
            return y_row

        if isinstance(widget, PianoRollWidget):
            return self._render_pianoroll(widget, frame, y_row)

        # Unknown widget type — skip one row
        return y_row + 1

    def _render_pianoroll(
        self, widget: PianoRollWidget, frame: Frame, y_row: int
    ) -> int:
        """Render the piano roll with pixel-resolution coloured note bars."""
        perf_t0 = time.monotonic()
        comp = self.comp
        cw, cell_h = comp.char_w, comp.char_h
        LEFT_CHARS = max(1, int(getattr(widget, "left_margin", 10)))
        perf_cfg = self._pr_perf_cfg
        perf_tier = self._pr_perf_tier
        effects = perf_cfg.get("effects", {})
        overlap_flash_enabled = bool(effects.get("overlap_flash", True))
        # Keep core piano-roll visuals stable even if adaptive perf tier moves.
        # Tier oscillation can otherwise look like grid/backlight pulsing.
        row_fade_enabled = bool(effects.get("row_fade", True))
        dotted_guides_enabled = bool(effects.get("dotted_guides", True))
        bars_only_mode = False
        row_guide_step = 4
        row_guide_stride = 1
        bar_guide_step = 3

        # --- Timeline row ---
        px_y = (PAGE_Y_OFFSET + y_row) * cell_h
        comp.text(0, px_y, f"{'Bars':>7} \u2502", fg=GREEN_DIM)
        roll_cols = len(widget.timeline)
        ticks_per_col = max(1, int(getattr(widget, "ticks_per_col", 1)))
        tick_anchor = int(getattr(widget, "tick_now", getattr(widget, "tick_right", 0)))
        tick_left = tick_anchor - max(1, roll_cols - 1) * ticks_per_col
        tick_right_edge = tick_anchor + ticks_per_col
        if roll_cols > 0:
            px_per_tick = cw / ticks_per_col
            x_left = LEFT_CHARS * cw
            x_right = x_left + roll_cols * cw
            bar_ticks = 24 * 4
            beat_ticks = 24
            first_bar = ((tick_left + bar_ticks - 1) // bar_ticks) * bar_ticks
            first_beat = ((tick_left + beat_ticks - 1) // beat_ticks) * beat_ticks
            t = first_bar
            while t <= tick_right_edge:
                px_x = x_left + int(round((t - tick_left) * px_per_tick))
                if x_left <= px_x < x_right:
                    comp.rect(px_x, px_y, 1, cell_h, GREEN_MID)
                t += bar_ticks
            if not bars_only_mode:
                t = first_beat
                while t <= tick_right_edge:
                    if t % bar_ticks != 0:
                        px_x = x_left + int(round((t - tick_left) * px_per_tick))
                        if x_left <= px_x < x_right:
                            comp.rect(px_x, px_y, 1, cell_h, GREEN_DIM)
                    t += beat_ticks
        y_row += 1

        pitch_high = widget.pitch_high if widget.pitch_high else (widget.pitches[0] if widget.pitches else None)
        pitch_low = widget.pitch_low if widget.pitch_low else (widget.pitches[-1] if widget.pitches else None)
        pitch_rows = min(len(widget.pitches), max(0, frame.rows - y_row))
        roll_top_px = (PAGE_Y_OFFSET + y_row) * cell_h
        roll_bottom_px = roll_top_px + (pitch_rows * cell_h)
        x0 = LEFT_CHARS * cw

        spans = getattr(widget, "spans", None) or []
        columns = getattr(widget, "columns", None) or []
        cc_lanes = getattr(widget, "cc_lanes", None) or []
        now_mono = time.monotonic()

        # Pitches with any visible note data in the current window.
        highlight_pitches = set()
        if pitch_high is not None and pitch_low is not None:
            if spans:
                for span in spans:
                    if not isinstance(span, (list, tuple)) or len(span) < 5:
                        continue
                    start_tick, end_tick, pitch, _channel, velocity = span[:5]
                    if velocity <= 0:
                        continue
                    if pitch > pitch_high or pitch < pitch_low:
                        continue
                    if end_tick < tick_left or start_tick > tick_right_edge:
                        continue
                    highlight_pitches.add(int(pitch))
            elif columns:
                for col_events in columns:
                    for pitch, _channel, velocity in col_events:
                        if velocity <= 0:
                            continue
                        if pitch_high >= pitch >= pitch_low:
                            highlight_pitches.add(int(pitch))

        # Refresh fade timers for pitches still visible in this window.
        if highlight_pitches:
            fade_until = now_mono + float(_ROLL_ACTIVE_ROW_FADE_S)
            for p in highlight_pitches:
                self._roll_row_fade_until[int(p)] = fade_until

        # Extend active-note highlighting across the roll area, then fade out
        # to zero over 1 second after a pitch leaves screen.
        if row_fade_enabled and pitch_rows > 0 and roll_cols > 0:
            row_w = max(1, roll_cols * cw)
            full_idx = int(_ROLL_ACTIVE_ROW_FADE_STEPS)
            for row_idx in range(pitch_rows):
                pitch = widget.pitches[row_idx]
                pitch_i = int(pitch)
                if pitch_i in highlight_pitches:
                    tint = int(self._roll_row_fade_lut[full_idx])
                else:
                    until = self._roll_row_fade_until.get(pitch_i)
                    if until is None:
                        continue
                    remain = float(until) - now_mono
                    if remain <= 0.0:
                        self._roll_row_fade_until.pop(pitch_i, None)
                        continue
                    frac = max(0.0, min(1.0, remain / float(_ROLL_ACTIVE_ROW_FADE_S)))
                    idx = int(round(frac * float(_ROLL_ACTIVE_ROW_FADE_STEPS)))
                    idx = max(1, min(full_idx, idx))
                    tint = int(self._roll_row_fade_lut[idx])
                px_y = roll_top_px + row_idx * cell_h
                comp.rect(x0, px_y, row_w, cell_h, tint)

        # --- Faint dotted pitch separators + brighter C-row separators ---
        if dotted_guides_enabled and pitch_rows > 0 and roll_cols > 0:
            x_left = x0
            x_right = x0 + roll_cols * cw
            buf = comp._buf
            for row_idx in range(1, pitch_rows, row_guide_stride):
                pitch = widget.pitches[row_idx]
                dot_y = roll_top_px + row_idx * cell_h
                if not (0 <= dot_y < buf.shape[0]):
                    continue
                dot_colour = _ROLL_H_DOT_C if (pitch % 12 == 0) else _ROLL_H_DOT
                buf[dot_y, x_left + (row_idx & 1):x_right:row_guide_step] = dot_colour

        # --- Vertical dotted bar guides through the full roll area ---
        if dotted_guides_enabled and pitch_rows > 0 and roll_cols > 0:
            ticks_per_col = max(1, int(getattr(widget, "ticks_per_col", 1)))
            tick_anchor = int(getattr(widget, "tick_now", getattr(widget, "tick_right", 0)))
            tick_left = tick_anchor - max(1, roll_cols - 1) * ticks_per_col
            tick_right_edge = tick_anchor + ticks_per_col
            px_per_tick = cw / ticks_per_col
            x_left = x0
            x_right = x0 + roll_cols * cw
            bar_ticks = 24 * 4
            first_bar = ((tick_left + bar_ticks - 1) // bar_ticks) * bar_ticks
            t = first_bar
            while t <= tick_right_edge:
                px_x = x_left + int(round((t - tick_left) * px_per_tick))
                if x_left <= px_x < x_right:
                    comp._buf[roll_top_px:roll_bottom_px:bar_guide_step, px_x] = _ROLL_V_BAR_DOT
                t += bar_ticks

        # --- Note rows ---
        NOTE_NAMES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
        for pitch in widget.pitches:
            if y_row >= frame.rows:
                break
            px_y = (PAGE_Y_OFFSET + y_row) * cell_h

            note_name = f"{NOTE_NAMES[pitch % 12]}{(pitch // 12) - 1}"
            is_c = (pitch % 12 == 0)
            label = f"{note_name:>7} \u2502"
            if int(pitch) in highlight_pitches:
                comp.rect(0, px_y, LEFT_CHARS * cw, cell_h, GREEN_MID)
                comp.text(0, px_y, label, fg=BG)
            else:
                comp.text(
                    0, px_y,
                    label,
                    fg=GREEN_BRIGHT if is_c else GREEN_DIM,
                )
            y_row += 1

        # --- Note bars (continuous spans preferred) ---
        roll_top_px = (PAGE_Y_OFFSET + (y_row - len(widget.pitches))) * cell_h
        _BAR_INSET = 1  # dark outer-ring width; overlap = dark edge of intruding note cuts through neighbour's bright fill
        if spans and pitch_high is not None and pitch_low is not None:
            roll_cols = len(widget.timeline)
            ticks_per_col = max(1, int(getattr(widget, "ticks_per_col", 1)))
            tick_anchor = int(getattr(widget, "tick_now", getattr(widget, "tick_right", 0)))
            tick_left = tick_anchor - max(1, roll_cols - 1) * ticks_per_col
            tick_right_edge = tick_anchor + ticks_per_col
            px_per_tick = cw / ticks_per_col
            x_left = x0
            x_right = x0 + roll_cols * cw
            # Track drawn pixel intervals per pitch for overlap flash.
            # Using drawn geometry avoids tick-domain bridge artefacts.
            overlap_drawn: dict[int, list[tuple[int, int, int, int, int]]] = {}
            for span in spans:
                if not isinstance(span, (list, tuple)) or len(span) < 5:
                    continue
                start_tick, end_tick, pitch, channel, velocity = span[:5]
                orig_start_tick = int(span[5]) if len(span) >= 6 else int(start_tick)
                if velocity <= 0:
                    continue
                if pitch > pitch_high or pitch < pitch_low:
                    continue
                if end_tick < tick_left or start_tick > tick_right_edge:
                    continue
                row_idx = pitch_high - pitch
                px_y = roll_top_px + row_idx * cell_h
                px_start = x_left + int(round((start_tick - tick_left) * px_per_tick))
                px_end = x_left + int(round((end_tick - tick_left) * px_per_tick))
                if px_end < px_start:
                    px_start, px_end = px_end, px_start
                px_start = max(x_left, min(x_right, px_start))
                px_end = max(x_left, min(x_right, px_end))
                width = max(5, px_end - px_start)
                if px_start >= x_right or px_end <= x_left:
                    continue
                if px_start + width > x_right:
                    width = max(1, x_right - px_start)
                ch_idx = (int(channel) - 1) if channel is not None else 0
                color = self._vel_lut[ch_idx % 16][min(int(velocity), 127)]
                bar_h = cell_h
                px_stop = px_start + width
                if px_stop <= px_start:
                    continue
                comp.rect(px_start, px_y, width, bar_h, color)
                overlap_drawn.setdefault(int(pitch), []).append(
                    (int(px_start), int(px_stop), int(channel or 1), int(velocity), int(orig_start_tick))
                )
                # Dark 1px outer ring — bright fill inside, dark edge outside.
                # Where notes overlap, the dark ring of the intruding note cuts
                # visibly through the bright fill of the note beneath it.
                if not bars_only_mode and width > 2 * _BAR_INSET and bar_h > 2 * _BAR_INSET:
                    comp.rect(px_start,                      px_y,              _BAR_INSET, bar_h, BG)
                    comp.rect(px_start + width - _BAR_INSET, px_y,              _BAR_INSET, bar_h, BG)
                    comp.rect(px_start + _BAR_INSET,         px_y,              width - 2 * _BAR_INSET, _BAR_INSET, BG)
                    comp.rect(px_start + _BAR_INSET,         px_y + bar_h - _BAR_INSET, width - 2 * _BAR_INSET, _BAR_INSET, BG)

            # --- Overlap flash pass ---
            # Sweep over pixel intervals from the bars we actually drew.
            # This prevents false bridging in empty space.
            if overlap_flash_enabled:
                _FLASH_HZ = 16.0
                flash_t = time.monotonic()
                for opitch, raw_intervals in overlap_drawn.items():
                    # Use all drawn intervals for this pitch; pixel-space overlap only
                    # flashes where bars actually intersect on-screen.
                    intervals = list(raw_intervals)
                    if len(intervals) < 2:
                        continue
                    # Deduplicate same note press (orig_start + channel), keeping longer end.
                    seen_keys: dict[tuple[int, int], int] = {}
                    deduped: list[tuple[int, int, int, int, int]] = []
                    for entry in intervals:
                        key = (int(entry[4]), int(entry[2]))  # (orig_start, ch)
                        idx = seen_keys.get(key)
                        if idx is None:
                            seen_keys[key] = len(deduped)
                            deduped.append(entry)
                        elif int(entry[1]) > int(deduped[idx][1]):
                            deduped[idx] = entry
                    pspans = deduped
                    if len(pspans) < 2:
                        continue
                    # Sweep events: (x, type, idx); type 0=end before 1=start at same x.
                    events: list[tuple] = []
                    for oi, (x0_i, x1_i, _ch, _vel, _orig_st) in enumerate(pspans):
                        events.append((int(x0_i), 1, oi))
                        events.append((int(x1_i), 0, oi))
                    events.sort()
                    active: set[int] = set()
                    prev_x: int | None = None
                    orow_idx = pitch_high - opitch
                    opx_y = roll_top_px + orow_idx * cell_h
                    for ox, etype, oidx in events:
                        if prev_x is not None and len(active) >= 2 and ox > prev_x:
                            opx_s = max(x_left, min(x_right, int(prev_x)))
                            opx_e = max(x_left, min(x_right, int(ox)))
                            ow = opx_e - opx_s
                            if ow > 0:
                                active_list = sorted(active)
                                n = len(active_list)
                                total_phases = n + 1
                                # User-tuned overlap speed map (relative to current base):
                                # slowest: 70%, 2-note: 90%, 3-note: 130%, 4+-note: 170%.
                                # Note: overlap flash renders only when >=2 notes are active.
                                if n >= 4:
                                    flash_mult = 1.70
                                elif n == 3:
                                    flash_mult = 1.30
                                elif n == 2:
                                    flash_mult = 0.90
                                else:
                                    flash_mult = 0.70
                                flash_hz = _FLASH_HZ * flash_mult
                                phase_idx = int(flash_t * flash_hz) % total_phases
                                if phase_idx < n:
                                    si = active_list[phase_idx]
                                    ach, avel = pspans[si][2], pspans[si][3]
                                    ach_idx = (int(ach) - 1) % 16
                                    ocolor = self._vel_lut[ach_idx][min(int(avel), 127)]
                                else:
                                    ocolor = BG
                                comp.rect(opx_s, opx_y, ow, cell_h, ocolor)
                        if etype == 1:
                            active.add(oidx)
                        else:
                            active.discard(oidx)
                        prev_x = ox
        else:
            if columns and pitch_high is not None and pitch_low is not None:
                for i, col_events in enumerate(columns):
                    if not col_events:
                        continue
                    px_x = x0 + i * cw + 1
                    for pitch, channel, velocity in col_events:
                        if velocity <= 0:
                            continue
                        if pitch > pitch_high or pitch < pitch_low:
                            continue
                        row_idx = pitch_high - pitch
                        px_y = roll_top_px + row_idx * cell_h
                        ch_idx = (int(channel) - 1) if channel is not None else 0
                        color = self._vel_lut[ch_idx % 16][min(int(velocity), 127)]
                        bar_w = cw - 1
                        bar_h = cell_h
                        comp.rect(px_x, px_y, bar_w, bar_h, color)
                        if not bars_only_mode and bar_w > 2 * _BAR_INSET and bar_h > 2 * _BAR_INSET:
                            comp.rect(px_x,                      px_y,             _BAR_INSET, bar_h, BG)
                            comp.rect(px_x + bar_w - _BAR_INSET, px_y,             _BAR_INSET, bar_h, BG)
                            comp.rect(px_x + _BAR_INSET,         px_y,             bar_w - 2 * _BAR_INSET, _BAR_INSET, BG)
                            comp.rect(px_x + _BAR_INSET,         px_y + bar_h - _BAR_INSET, bar_w - 2 * _BAR_INSET, _BAR_INSET, BG)
            else:
                # Fallback: dense grid rendering (legacy widget.cells)
                for row_idx, row_cells in enumerate(widget.cells):
                    px_y = roll_top_px + row_idx * cell_h
                    for i, cell in enumerate(row_cells):
                        if cell.velocity > 0:
                            ch_idx = (int(cell.channel) - 1) if cell.channel is not None else 0
                            color = self._vel_lut[ch_idx % 16][min(cell.velocity, 127)]
                            comp.rect(x0 + i * cw + 1, px_y + 1, cw - 1, cell_h - 2, color)

        # --- CC lanes (page 16 memory mode): native pixel bars, no ASCII ramp ---
        if cc_lanes and roll_cols > 0:
            x_left = x0
            x_right = x0 + roll_cols * cw
            for lane in cc_lanes:
                if y_row >= frame.rows:
                    break
                px_y = (PAGE_Y_OFFSET + y_row) * cell_h
                comp.rect(0, px_y, LEFT_CHARS * cw, cell_h, _CC_LANE_BG)
                cc_num = int(lane.get("cc", 0))
                ch_num = int(lane.get("ch", 1))
                label = f"CC{cc_num:03d}:{ch_num:02d} \u2502"
                comp.text(0, px_y, label, fg=GREEN_DIM)
                # Baseline + bar guides.
                comp.rect(x_left, px_y + cell_h - 1, max(1, x_right - x_left), 1, _ROLL_H_DOT)
                values = lane.get("values", [])
                if isinstance(values, list):
                    for i, raw_v in enumerate(values[:roll_cols]):
                        v = int(raw_v) if raw_v is not None else -1
                        if v < 0:
                            continue
                        v = max(0, min(127, v))
                        h = 1 + int((v / 127.0) * max(1, cell_h - 2))
                        bar_x = x_left + i * cw + 1
                        bar_w = max(1, cw - 2)
                        bar_y = px_y + cell_h - 1 - h
                        bar_col = _CC_LANE_BAR_HI if v >= 96 else _CC_LANE_BAR
                        comp.rect(bar_x, bar_y, bar_w, h, bar_col)
                y_row += 1

        self._update_pianoroll_perf_tier((time.monotonic() - perf_t0) * 1000.0)
        return y_row

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        self.comp.close()
