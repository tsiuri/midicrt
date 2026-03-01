# pages/img2txtviz.py - MIDI + spectrum reactive ASCII image translator
BACKGROUND = True
PAGE_ID = 17
PAGE_NAME = "MIDI IMG2TXT"

import math
import time
from typing import Dict, Tuple

from midicrt import draw_line
from pages.legacy_contract_bridge import build_widget_from_legacy_contract

try:
    from pages import audiospectrum as _audiospectrum
except Exception:
    _audiospectrum = None


_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_CHARSETS = (
    " .:-=+*#%@",
    "  .,:;irsXA253hMHGS#9B&@",
    " .'`^\",:;Il!i~+_-?][}{1)(|/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$",
    " .oO0@",
)
_TWO_PI = 2.0 * math.pi
_SIN_LUT_SIZE = 4096
_SIN_LUT_MASK = _SIN_LUT_SIZE - 1
_SIN_LUT_SCALE = _SIN_LUT_SIZE / _TWO_PI
_SIN_LUT = [math.sin((_TWO_PI * i) / _SIN_LUT_SIZE) for i in range(_SIN_LUT_SIZE)]

_audio_ready = False
_active_notes: Dict[Tuple[int, int], int] = {}
_last_note = 60
_last_vel = 0
_last_program = 0
_cc: Dict[int, int] = {}
_energy = 0.0
_spark = 0.0
_vel_splash = 0.0
_last_note_on_t = 0.0
_last_decay_t = time.monotonic()

_manual_block = 2
_invert = False
_charset_ix = 0
_gamma = 1.0
_trail = []
_trail_w = 0
_trail_h = 0
_last_audio_seq = -1
_audio_rms = 0.0
_audio_env = 0.0
_audio_src = "none"
_target_fps = 60.0
_last_render_t = 0.0
_last_render_ms = 0.0
_cached_rows = []
_cached_meta = {}
_base_darkness = 0.52
_stim_threshold = 0.26
_audio_enabled = True
_quality_boost = 0
_quality_max = 3
_auto_quality = False
_return_speed_mult = 3.0
_last_audio_env_t = time.monotonic()
_last_layout = None
_last_title_line = ""
_last_info_line1 = ""
_last_info_line2 = ""
_last_footer_line = ""
_last_ascii_rows = []
_flush_dirty = True
_render_ms_ema = 0.0
_quality_last_adjust_t = 0.0


def _ensure_audio():
    global _audio_ready
    if not _audio_enabled:
        return
    if _audio_ready or _audiospectrum is None:
        return
    try:
        if hasattr(_audiospectrum, "register_spectrum_tap"):
            _audiospectrum.register_spectrum_tap(PAGE_ID)
        if hasattr(_audiospectrum, "register_raw_tap"):
            _audiospectrum.register_raw_tap(PAGE_ID)
        if hasattr(_audiospectrum, "ensure_background"):
            _audiospectrum.ensure_background()
        _audio_ready = True
    except Exception:
        pass


def _clamp(v: float, lo: float, hi: float) -> float:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _note_name(note: int) -> str:
    n = int(note)
    return f"{_NOTE_NAMES[n % 12]}{(n // 12) - 1}"


def _decay():
    global _last_decay_t, _energy, _spark, _vel_splash
    now = time.monotonic()
    dt = max(0.0, now - _last_decay_t)
    _last_decay_t = now
    _energy *= math.exp(-dt * (1.35 * _return_speed_mult))
    _spark *= math.exp(-dt * (2.2 * _return_speed_mult))
    # Fast transient burst that creates visible "splash" on high-velocity hits.
    _vel_splash *= math.exp(-dt * (8.0 * _return_speed_mult))
    if _energy < 0.0001:
        _energy = 0.0
    if _spark < 0.0001:
        _spark = 0.0
    if _vel_splash < 0.0001:
        _vel_splash = 0.0


def handle(msg):
    global _last_note, _last_vel, _last_program, _energy, _spark, _vel_splash, _last_note_on_t
    _decay()
    try:
        mtype = str(getattr(msg, "type", ""))
    except Exception:
        return
    if mtype == "note_on":
        note = int(getattr(msg, "note", 0))
        vel = int(getattr(msg, "velocity", 0))
        ch = int(getattr(msg, "channel", 0))
        key = (ch, note)
        if vel > 0:
            _active_notes[key] = vel
            _last_note = note
            _last_vel = vel
            vel01 = _clamp(vel / 127.0, 0.0, 1.0)
            # Velocity-nonlinear weighting: high velocities produce much larger bursts.
            vel_w = pow(vel01, 1.85)
            now = time.monotonic()
            dt_note = now - _last_note_on_t if _last_note_on_t > 0.0 else 0.12
            _last_note_on_t = now
            # Rate sensitivity: dense note streams should not dominate excitation.
            rate_scale = _clamp(dt_note / 0.10, 0.30, 1.0)
            _energy = _clamp(_energy + (vel_w * (0.35 + (0.65 * rate_scale))), 0.0, 2.6)
            _spark = _clamp(_spark + (0.18 + (vel_w * 0.95)), 0.0, 2.8)
            # Velocity splash is intentionally less rate-dependent for per-hit impact.
            _vel_splash = _clamp(_vel_splash + (vel_w * 1.75), 0.0, 3.2)
        else:
            _active_notes.pop(key, None)
    elif mtype == "note_off":
        note = int(getattr(msg, "note", 0))
        ch = int(getattr(msg, "channel", 0))
        _active_notes.pop((ch, note), None)
    elif mtype == "control_change":
        ctrl = int(getattr(msg, "control", 0))
        val = int(getattr(msg, "value", 0))
        _cc[ctrl] = int(_clamp(val, 0, 127))
    elif mtype == "program_change":
        _last_program = int(getattr(msg, "program", 0)) % 128


def on_tick(_state):
    if _audio_enabled:
        _ensure_audio()
    _decay()


def keypress(ch):
    global _manual_block, _invert, _charset_ix, _gamma, _audio_enabled, _audio_env, _audio_src
    global _target_fps, _auto_quality, _quality_boost
    s = str(ch)
    kname = ""
    try:
        if getattr(ch, "is_sequence", False):
            kname = str(getattr(ch, "name", "") or "")
    except Exception:
        kname = ""
    if s == "[":
        _manual_block = max(1, _manual_block - 1)
        return True
    if s == "]":
        _manual_block = min(5, _manual_block + 1)
        return True
    if s.lower() == "i":
        _invert = not _invert
        return True
    if s.lower() == "c":
        _charset_ix = (_charset_ix + 1) % len(_CHARSETS)
        return True
    if s.lower() == "a":
        _audio_enabled = not _audio_enabled
        if not _audio_enabled:
            _audio_env = 0.0
            _audio_src = "midi-only"
        return True
    if s.lower() == "j":
        _target_fps = _clamp(_target_fps - 2.0, 8.0, 60.0)
        return True
    if s.lower() == "k":
        _target_fps = _clamp(_target_fps + 2.0, 8.0, 60.0)
        return True
    if s.lower() == "u":
        _auto_quality = not _auto_quality
        if not _auto_quality:
            _quality_boost = 0
        return True
    if s.lower() == "g" or s == "-" or s == "_" or kname in {"KEY_MINUS", "KEY_KP_SUBTRACT"}:
        _gamma = _clamp(_gamma - 0.1, 0.6, 2.2)
        return True
    if s.lower() == "h" or s == "=" or s == "+" or kname in {"KEY_PLUS", "KEY_KP_ADD"}:
        _gamma = _clamp(_gamma + 0.1, 0.6, 2.2)
        return True
    return False


def _audio_levels():
    if not _audio_enabled:
        return []
    if _audiospectrum is None or not hasattr(_audiospectrum, "get_levels"):
        return []
    try:
        return list(_audiospectrum.get_levels())
    except Exception:
        return []


def _audio_activity(levels):
    global _last_audio_seq, _audio_rms, _audio_env, _audio_src, _last_audio_env_t
    now = time.monotonic()
    dt = max(0.0, now - _last_audio_env_t)
    _last_audio_env_t = now
    if not _audio_enabled:
        _audio_rms = 0.0
        _audio_env = 0.0
        _audio_src = "midi-only"
        return 0.0, 0.0, 0.0
    spec_peak = max(levels) if levels else 0.0
    raw_mapped = 0.0
    has_raw = False
    if _audiospectrum is not None and hasattr(_audiospectrum, "get_last_audio_block"):
        try:
            block, seq, _sr, _ts = _audiospectrum.get_last_audio_block()
        except Exception:
            block, seq = None, _last_audio_seq
        try:
            seq_i = int(seq)
        except Exception:
            seq_i = _last_audio_seq
        if block is not None and seq_i != _last_audio_seq:
            _last_audio_seq = seq_i
            n = len(block)
            if n > 0:
                acc = 0.0
                for v in block:
                    fv = float(v)
                    acc += fv * fv
                rms = math.sqrt(acc / float(n))
                _audio_rms = rms
                raw_mapped = _clamp(rms * 24.0, 0.0, 1.0)
                has_raw = True
    # Keep audio envelope decay stable across frame-rates; tuned to return
    # about 3x faster than before.
    raw_hold_rate = 2.528 * _return_speed_mult
    idle_decay_rate = 1.231 * _return_speed_mult
    if has_raw:
        _audio_env = max(raw_mapped, _audio_env * math.exp(-dt * raw_hold_rate))
    else:
        _audio_env *= math.exp(-dt * idle_decay_rate)
    _audio_env = _clamp(_audio_env, 0.0, 1.0)
    drive = max(spec_peak, _audio_env)
    if drive > 0.01:
        _audio_src = "raw+spec" if (levels and _audio_env > 0.0) else ("raw" if _audio_env > 0.0 else "spec")
    else:
        _audio_src = "none"
    return _clamp(spec_peak, 0.0, 1.0), _audio_env, _clamp(drive, 0.0, 1.0)


def _sample_audio(levels, x01: float) -> float:
    if not levels:
        return 0.0
    idx = int(x01 * (len(levels) - 1))
    return _clamp(float(levels[idx]), 0.0, 1.0)


def _render_ascii(width: int, height: int, levels, audio_drive: float):
    global _trail, _trail_w, _trail_h
    t = time.monotonic()
    cc1 = _cc.get(1, 0) / 127.0
    cc74 = _cc.get(74, 64) / 127.0
    cc71 = _cc.get(71, 64) / 127.0
    block_from_cc = 1 + int((1.0 - cc74) * 2.999)
    block = max(1, min(6, ((_manual_block + block_from_cc) // 2) + int(_quality_boost)))
    cw = max(8, width // block)
    ch = max(6, height // block)
    size = cw * ch
    if size <= 0:
        return [" " * width for _ in range(height)], block
    if _trail_w != cw or _trail_h != ch or len(_trail) != size:
        _trail = [0.0] * size
        _trail_w = cw
        _trail_h = ch

    note_phase = (_last_note % 12) / 12.0
    octave_phase = ((_last_note // 12) % 8) / 8.0
    act = len(_active_notes)
    active_boost = _clamp(act / 8.0, 0.0, 1.0)
    # Velocity splash dominates transient excitement; note-count impact is reduced.
    stim_raw = _clamp((audio_drive * 0.52) + (_energy * 0.15) + (_vel_splash * 0.72) + (active_boost * 0.05), 0.0, 1.0)
    stim = _clamp((stim_raw - _stim_threshold) / max(0.001, (1.0 - _stim_threshold)), 0.0, 1.0)
    stim = stim * stim
    speed = 0.45 + (cc1 * 1.7) + (_energy * 0.14) + (audio_drive * 1.2) + (_vel_splash * 1.45)
    contrast = 0.70 + (cc74 * 1.15) + (_energy * 0.18) + (audio_drive * 0.85) + (_vel_splash * 1.05)
    edge_mix = 0.1 + (cc71 * 0.65) + (audio_drive * 0.2)
    trail_decay = 0.76 + (0.2 * (1.0 - cc1))
    trail_decay = pow(trail_decay, _return_speed_mult)
    charset = _CHARSETS[(_charset_ix + _last_program) % len(_CHARSETS)]
    nchar = max(2, len(charset))

    rows = []
    cheap_mode = int(_quality_boost) >= 2
    prev = [0.0] * cw
    nxs = [x / max(1, cw - 1) for x in range(cw)]
    nys = [y / max(1, ch - 1) for y in range(ch)]
    phase_x = (t * speed) + (note_phase * 6.283)
    phase_y = (-t * (0.35 + cc74)) + (octave_phase * 6.283)
    pulse_phase = t * (4.0 + audio_drive * 12.0)
    ctr_x = 0.5 + 0.12 * math.sin(t * 0.31 + note_phase * 6.283)
    ctr_y = 0.5 + 0.12 * math.cos(t * 0.27 + octave_phase * 6.283)
    ring_rate = 1.3 + (_energy * 0.45) + (audio_drive * 0.55) + (_vel_splash * 1.25)
    ring_scale = 20.0
    ring_audio = 32.0
    energy_phase = t * 5.5
    lut = _SIN_LUT
    lut_mask = _SIN_LUT_MASK
    lut_scale = _SIN_LUT_SCALE
    wave_x_cache = [0.5 + 0.5 * lut[int(((nx * 7.0) + phase_x) * lut_scale) & lut_mask] for nx in nxs]
    energy_x_cache = [lut[int(((nx * 13.0) + energy_phase) * lut_scale) & lut_mask] for nx in nxs]
    if levels:
        a_idx_scale = (len(levels) - 1) / max(1, cw - 1)
        a_by_x = [float(levels[int(x * a_idx_scale)]) for x in range(cw)]
    else:
        a_by_x = [0.0] * cw
    for y in range(ch):
        line_chars = []
        cur = [0.0] * cw
        ny = nys[y]
        wave_y = 0.5 + 0.5 * lut[int(((ny * 9.0) + phase_y) * lut_scale) & lut_mask]
        pulse = 0.5 + 0.5 * lut[int((pulse_phase + (ny * 8.0)) * lut_scale) & lut_mask]
        for x in range(cw):
            nx = nxs[x]
            a = a_by_x[x]
            a_mix = _clamp((0.65 * a) + (0.35 * audio_drive), 0.0, 1.0)
            wave_x = wave_x_cache[x]
            # Manhattan distance approximation is cheaper than sqrt() and visually similar here.
            d = abs(nx - ctr_x) + abs(ny - ctr_y)
            ring = 0.5 + 0.5 * lut[int(((d * (ring_scale + ring_audio * a_mix)) - (t * ring_rate)) * lut_scale) & lut_mask]
            shimmer = 0.0 if cheap_mode else (0.5 + 0.5 * lut[int((((nx + ny) * (7.0 + 4.0 * active_boost) + t * (1.8 + _spark * 2.2)) * lut_scale)) & lut_mask])
            v = (0.22 * wave_x) + (0.18 * wave_y) + (0.22 * ring) + (0.12 * shimmer) + (0.44 * a_mix) + (0.18 * pulse * audio_drive)
            if _energy > 0.0:
                v += _energy * (0.08 + 0.12 * energy_x_cache[x])
            if _vel_splash > 0.0:
                v += _vel_splash * (0.06 + (0.18 * ring))

            if (not cheap_mode) and (x > 0 or y > 0):
                eh = abs(v - (cur[x - 1] if x > 0 else v))
                ev = abs(v - (prev[x] if y > 0 else v))
                edge = _clamp((eh + ev) * 1.8, 0.0, 1.0)
                v = (1.0 - edge_mix) * v + edge_mix * edge

            v = (v - 0.5) * contrast + 0.5
            v = _clamp(v, 0.0, 1.0)

            idx = y * cw + x
            tv = max(v, _trail[idx] * trail_decay)
            _trail[idx] = tv
            cur[x] = tv

            out = _clamp(tv, 0.0, 1.0)
            out *= (_base_darkness + (1.0 - _base_darkness) * stim)
            if _gamma != 1.0:
                out = _clamp(pow(out, _gamma), 0.0, 1.0)
            if _invert:
                out = 1.0 - out
            ci = int(out * (nchar - 1) + 0.5)
            if ci < 0:
                ci = 0
            if ci >= nchar:
                ci = nchar - 1
            line_chars.append(charset[ci])
        prev = cur
        expanded = "".join(chv * block for chv in line_chars)[:width].ljust(width)
        for _ in range(block):
            if len(rows) < height:
                rows.append(expanded)
    while len(rows) < height:
        rows.append(" " * width)
    return rows, block


def draw(state):
    global _last_render_t, _last_render_ms, _cached_rows, _cached_meta
    global _quality_boost, _render_ms_ema, _quality_last_adjust_t
    global _last_layout, _last_title_line, _last_info_line1, _last_info_line2, _last_footer_line, _last_ascii_rows, _flush_dirty
    if _audio_enabled:
        _ensure_audio()
    _decay()

    cols = int(state.get("cols", 95))
    rows = int(state.get("rows", 30))
    y0 = int(state.get("y_offset", 3))
    width = max(1, cols - 2)

    levels = _audio_levels()
    spec_peak, raw_env, audio_drive = _audio_activity(levels)
    if not _audio_enabled:
        midi_drive = _clamp((_energy * 0.70) + (_clamp(len(_active_notes) / 8.0, 0.0, 1.0) * 0.40), 0.0, 1.0)
        audio_drive = max(audio_drive, midi_drive)
    if not levels and audio_drive > 0.01:
        levels = [audio_drive] * max(16, min(96, width // 2))
    cc1 = int(_cc.get(1, 0))
    cc74 = int(_cc.get(74, 0))

    title_line = f"--- {PAGE_NAME} ---".ljust(cols)
    line1 = (
        f"Active:{len(_active_notes):02d}  Last:{_note_name(_last_note):>3} v{_last_vel:03d}  "
        f"Energy:{_energy:0.2f}  Splash:{_vel_splash:0.2f}  Spec:{spec_peak:0.2f}  Raw:{raw_env:0.2f}  Drive:{audio_drive:0.2f}  "
        f"CC1:{cc1:03d}  CC74:{cc74:03d}  PC:{_last_program:03d}  Src:{_audio_src}"
    )
    line2 = (
        f"Controls: [ ] block  c charset  i invert  g/h gamma ({_gamma:0.1f})  a audio({('on' if _audio_enabled else 'off')})  "
        f"j/k cap({int(_target_fps):02d})  u autoQ({('on' if _auto_quality else 'off')})  Mode:{'inv' if _invert else 'norm'}"
    )
    info_line1 = line1[:cols]
    info_line2 = line2[:cols]

    top = y0 + 3
    bottom = rows - 5
    height = max(1, bottom - top + 1)
    now = time.monotonic()
    render_interval = 1.0 / max(1.0, float(_target_fps))
    reuse_cache = (
        bool(_cached_rows)
        and (now - _last_render_t) < render_interval
        and _cached_meta.get("width") == width
        and _cached_meta.get("height") == height
        and _cached_meta.get("block") is not None
    )
    if reuse_cache:
        ascii_rows = _cached_rows
        block = int(_cached_meta.get("block", _manual_block))
        bins_len = int(_cached_meta.get("bins", len(levels)))
        chars_len = int(_cached_meta.get("chars", len(_CHARSETS[(_charset_ix + _last_program) % len(_CHARSETS)])))
    else:
        t0 = time.monotonic()
        ascii_rows, block = _render_ascii(width, height, levels, audio_drive)
        _last_render_ms = (time.monotonic() - t0) * 1000.0
        _last_render_t = now
        bins_len = len(levels)
        chars_len = len(_CHARSETS[(_charset_ix + _last_program) % len(_CHARSETS)])
        _cached_rows = list(ascii_rows)
        _cached_meta = {
            "width": width,
            "height": height,
            "block": block,
            "bins": bins_len,
            "chars": chars_len,
        }
        if _render_ms_ema <= 0.0:
            _render_ms_ema = _last_render_ms
        else:
            _render_ms_ema = (0.80 * _render_ms_ema) + (0.20 * _last_render_ms)
        if _auto_quality:
            # Stabilized quality adaptation: hysteresis + cooldown to avoid
            # oscillating detail level (which causes perceived fps spikes/dips).
            budget_ms = 1000.0 / max(1.0, float(_target_fps))
            if (now - _quality_last_adjust_t) >= 0.50:
                if _render_ms_ema > (budget_ms * 1.20):
                    _quality_boost = min(_quality_max, _quality_boost + 1)
                    _quality_last_adjust_t = now
                elif _render_ms_ema < (budget_ms * 0.70):
                    _quality_boost = max(0, _quality_boost - 1)
                    _quality_last_adjust_t = now

    footer_line = f"Img2txt block:{block}  bins:{bins_len:03d}  chars:{chars_len:03d}  rend:{_last_render_ms:5.1f}ms  cap:{int(_target_fps):02d}fps  q:{_quality_boost}"[:cols]
    pad = " "
    layout = (cols, rows, y0, top, height, width)
    force_full = layout != _last_layout
    dirty = False

    if force_full or title_line != _last_title_line:
        draw_line(y0, title_line)
        _last_title_line = title_line
        dirty = True
    if force_full or info_line1 != _last_info_line1:
        draw_line(y0 + 1, info_line1)
        _last_info_line1 = info_line1
        dirty = True
    if force_full or info_line2 != _last_info_line2:
        draw_line(y0 + 2, info_line2)
        _last_info_line2 = info_line2
        dirty = True

    # Row-diff render: update only changed body rows.
    if force_full and _last_layout is not None:
        old_top = int(_last_layout[3])
        old_h = int(_last_layout[4])
        for y in range(old_top, old_top + old_h):
            draw_line(y, " " * cols)
        dirty = True
    if force_full or len(_last_ascii_rows) != len(ascii_rows):
        for i, row in enumerate(ascii_rows):
            draw_line(top + i, (pad + row)[:cols])
        dirty = True
    else:
        for i, row in enumerate(ascii_rows):
            if row != _last_ascii_rows[i]:
                draw_line(top + i, (pad + row)[:cols])
                dirty = True
    _last_ascii_rows = list(ascii_rows)

    if force_full or footer_line != _last_footer_line:
        draw_line(rows - 4, footer_line)
        _last_footer_line = footer_line
        dirty = True

    _last_layout = layout
    _flush_dirty = dirty


def compositor_cache_key():
    # Disable ui_loop whole-frame skip for this page.
    # Page 17 manages its own content-rate limiting/cache; returning None avoids
    # monitor "stuck frame" behavior seen on some systems with compositor skip.
    return None


def compositor_flush_hint():
    # Let compositor skip some flushes when nothing on this page changed.
    # Always flush at least ~30 Hz to avoid visible stalls.
    max_interval = 1.0 / min(30.0, max(10.0, float(_target_fps)))
    return {"dirty": bool(_flush_dirty), "max_interval": max_interval}


def build_widget(state):
    return build_widget_from_legacy_contract(draw, state, draw_line)
