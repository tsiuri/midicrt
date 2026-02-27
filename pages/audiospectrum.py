# pages/audiospectrum.py — Terminal audio spectrum visualizer (USB soundcard input)
BACKGROUND = True
PAGE_ID = 9
PAGE_NAME = "Audio Spectrum"

import sys
import os
import json
import threading
import time
from math import log10
from collections import deque
from blessed import Terminal
from midicrt import draw_line
from pages.legacy_contract_bridge import build_widget_from_legacy_contract
from ui.model import PageLinesWidget

term = Terminal()

# ------------- Config -------------
DEFAULT_SR = 44100
BLOCKSIZE = 1024            # frames per block
TARGET_BINS = 96            # default number of spectrum bars
SMOOTHING = 0.6             # exponential moving average for stability
GAIN_DB = 0.0               # user gain in dB
DISPLAY_SCALE = 0.75        # additional headroom to avoid top-row clipping
# Fixed dB mapping: widened range and normalized FFT so bars don't saturate
FLOOR_DB = -110.0
CEIL_DB  =   10.0   # allow headroom above 0 dB reference
# High-pass/DC-block settings to tame low-end clipping
FMIN_HZ = 40.0              # minimum frequency for log bands (low-cut)
HPF_HZ = 30.0               # high-pass cutoff (single-pole DC blocker)
HPF_ON = True               # enable/disable HPF/DC blocker
AUTO_ADAPT = False          # auto-adjust display scale toward a target
ADAPT_TARGET = 0.80         # aim tallest bar near this fraction of height
ADAPT_RATE = 0.03           # smoothing toward target per callback
SCALE_MIN = 0.2             # lower bound for auto display scale
SCALE_MAX = 1.0             # upper bound for auto display scale

# ------------- State --------------
_audio_thread = None
_stop_event = threading.Event()
_last_thread_start = 0.0
_THREAD_COOLDOWN = 5.0  # seconds between restart attempts
_ready = False
_error_msg = None
_sr = DEFAULT_SR
_bins = TARGET_BINS
_gain_db = GAIN_DB
_smooth = SMOOTHING
_last_levels = []  # normalized [0..1]
_lock = threading.Lock()
_device_index = None   # None = default, else an int index into _inputs list
_inputs = []           # cached list of (index, name) tuples
_freq_scale = 'log'    # 'log' (default) or 'lin' frequency spacing
_agg_mode = 'max'      # 'max' (default) or 'avg' pooling per band
_auto_adapt = AUTO_ADAPT
_hpf_x_prev = 0.0
_hpf_y_prev = 0.0
_ACTIVE_PAGE_IDS = {PAGE_ID}
_RAW_PAGE_IDS = set()
_SPECTRUM_PAGE_IDS = {PAGE_ID}
_last_audio_block = None
_last_audio_seq = 0
_last_audio_ts = 0.0
_BG_SPECTRUM_HZ = 12.0
_BG_SPECTRUM_BINS = 48
_last_bg_spectrum_at = 0.0

# ------------- Spectrum cache (precomputed per-session constants) -------------
# Rebuilt when sr / blocksize / bins / freq_scale / FMIN_HZ change.
_cache_key  = None   # tuple of params that drive the cache
_cache_win  = None   # Hanning window (float32 array)
_cache_winp = 1.0    # sum(win**2)
_cache_starts = None # band start FFT-bin indices (int array, len=bins)
_cache_ends   = None # band end FFT-bin indices   (int array, len=bins)


def _rebuild_cache(sr, blocksize, bins, freq_scale, fmin_hz):
    """Recompute window and band-index arrays; store in module globals."""
    import numpy as np
    global _cache_key, _cache_win, _cache_winp, _cache_starts, _cache_ends
    key = (sr, blocksize, bins, freq_scale, fmin_hz)
    if key == _cache_key:
        return
    win = np.hanning(blocksize).astype(np.float32)
    winp = float(np.sum(win ** 2)) + 1e-12
    nsrc = blocksize // 2 + 1
    freqs = np.fft.rfftfreq(blocksize, d=1.0 / float(sr))
    target = max(1, int(bins))
    if freq_scale == 'lin':
        edges_i = np.linspace(0, nsrc, target + 1).astype(int)
        starts = edges_i[:-1]
        ends   = np.maximum(edges_i[1:], starts + 1)
    else:
        fmin = max(fmin_hz, float(freqs[1]) if freqs.size > 1 else fmin_hz)
        fmax = float(freqs[-1]) if freqs.size else sr / 2.0
        if fmax <= fmin:
            fmin = max(1.0, fmax * 0.5)
        edges = np.geomspace(fmin, fmax, target + 1)
        starts = np.searchsorted(freqs, edges[:-1]).astype(np.int32)
        ends   = np.searchsorted(freqs, edges[1:]).astype(np.int32)
        # For empty bands fall back to nearest bin
        empty = starts >= ends
        if empty.any():
            cf = np.sqrt(edges[:-1][empty] * edges[1:][empty])
            nearest = np.searchsorted(freqs, cf).clip(0, nsrc - 1).astype(np.int32)
            starts[empty] = nearest
            ends[empty]   = nearest + 1
    starts = starts.clip(0, nsrc - 1)
    ends   = ends.clip(0, nsrc)
    _cache_key, _cache_win, _cache_winp = key, win, winp
    _cache_starts, _cache_ends = starts, ends

# ------------- Config persistence -------------
CONFIG_FILE = 'settings.json'
CONFIG_SECTION = 'audiospectrum'
LEGACY_FILE = 'audiospectrum.json'
_cfg_loaded = False
_cfg_dirty = False
_cfg_last_save = 0.0

def _config_dir():
    base = os.environ.get('MIDICRT_CONFIG_DIR')
    if not base:
        # Project-local config dir: midicrt/config
        base = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config')
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        pass
    return base

def _config_path():
    return os.path.join(_config_dir(), CONFIG_FILE)

def _legacy_path():
    return os.path.join(_config_dir(), LEGACY_FILE)

def _apply_config(cfg):
    global FMIN_HZ, HPF_ON, DISPLAY_SCALE, FLOOR_DB, CEIL_DB
    global _freq_scale, _agg_mode, _auto_adapt, _bins, _gain_db, _smooth
    try:
        FMIN_HZ = float(cfg.get('lowcut_hz', FMIN_HZ))
    except Exception:
        pass
    HPF_ON = bool(cfg.get('hpf_on', HPF_ON))
    try:
        DISPLAY_SCALE = float(cfg.get('display_scale', DISPLAY_SCALE))
    except Exception:
        pass
    try:
        FLOOR_DB = float(cfg.get('floor_db', FLOOR_DB))
        CEIL_DB = float(cfg.get('ceil_db', CEIL_DB))
    except Exception:
        pass
    _freq_scale = cfg.get('freq_scale', _freq_scale)
    _agg_mode = cfg.get('agg_mode', _agg_mode)
    _auto_adapt = bool(cfg.get('auto_adapt', _auto_adapt))
    try:
        _bins = int(cfg.get('bins', _bins))
        _gain_db = float(cfg.get('gain_db', _gain_db))
        _smooth = float(cfg.get('smoothing', _smooth))
    except Exception:
        pass

def _load_config():
    global _cfg_loaded
    if _cfg_loaded:
        return
    cfg = None
    path = _config_path()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            root = json.load(f)
        if isinstance(root, dict):
            cfg = root.get(CONFIG_SECTION)
    except Exception:
        cfg = None

    if cfg is None:
        try:
            with open(_legacy_path(), 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            if isinstance(cfg, dict):
                _apply_config(cfg)
                # migrate legacy settings into shared config
                _mark_dirty()
        except Exception:
            cfg = None
    else:
        if isinstance(cfg, dict):
            _apply_config(cfg)
    _cfg_loaded = True

def _save_config():
    data = {
        'lowcut_hz': float(FMIN_HZ),
        'hpf_on': bool(HPF_ON),
        'display_scale': float(DISPLAY_SCALE),
        'floor_db': float(FLOOR_DB),
        'ceil_db': float(CEIL_DB),
        'freq_scale': _freq_scale,
        'agg_mode': _agg_mode,
        'auto_adapt': bool(_auto_adapt),
        'bins': int(_bins),
        'gain_db': float(_gain_db),
        'smoothing': float(_smooth),
    }
    try:
        root = {}
        path = _config_path()
        try:
            with open(path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
                if isinstance(existing, dict):
                    root = existing
        except Exception:
            pass
        root[CONFIG_SECTION] = data
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(root, f, indent=2)
    except Exception:
        pass

def _mark_dirty():
    global _cfg_dirty
    _cfg_dirty = True


def _try_imports():
    try:
        import numpy as np  # noqa: F401
        import sounddevice as sd  # noqa: F401
        return True
    except Exception as e:
        global _error_msg
        _error_msg = f"Missing deps: {e}. Install: pip install numpy sounddevice"
        return False


def _compute_spectrum(audio_block, sr, bins):
    """Return list of magnitudes (0..1) sized to bins, using fixed dB mapping."""
    import numpy as np
    # mono mix
    if audio_block.ndim == 2:
        x = audio_block.mean(axis=1)
    else:
        x = audio_block

    # simple DC blocker / first-order HPF
    # y[n] = (x[n] - x[n-1]) + R * y[n-1]  — vectorised via geometric prefix sum
    if HPF_ON and x.size:
        global _hpf_x_prev, _hpf_y_prev
        x = x.astype(np.float32)
        R = float(np.exp(-2.0 * np.pi * HPF_HZ / float(sr)))
        N = x.size
        d = np.empty(N, dtype=np.float64)
        d[0] = x[0] - _hpf_x_prev
        d[1:] = np.diff(x)
        r_pow = R ** np.arange(N, dtype=np.float64)
        d_scaled = d / np.where(r_pow == 0.0, 1.0, r_pow)
        y = r_pow * (np.cumsum(d_scaled) + _hpf_y_prev * R)
        _hpf_x_prev = float(x[-1])
        _hpf_y_prev = float(y[-1])
        x = y.astype(np.float32)

    # Rebuild precomputed cache if parameters changed
    _rebuild_cache(sr, len(x), bins, _freq_scale, FMIN_HZ)
    win  = _cache_win
    winp = _cache_winp

    # FFT
    xw = x * win
    xf = np.fft.rfft(xw)
    ps = (np.abs(xf) ** 2) / winp
    if ps.size:
        ps[0] = 0.0

    target = max(1, int(bins))
    nsrc = ps.size
    if nsrc <= 1:
        return [0.0] * target

    # Band reduction using precomputed start/end indices (no per-call mask ops)
    starts = _cache_starts
    ends   = _cache_ends
    band_val = np.zeros(target, dtype=np.float32)
    avg = (_agg_mode == 'avg')
    for i in range(target):
        seg = ps[starts[i]:ends[i]]
        band_val[i] = float(seg.mean() if avg else seg.max())

    eps = 1e-15
    db = 10.0 * np.log10(np.maximum(band_val, eps))
    norm = (db - FLOOR_DB) / max(1e-6, (CEIL_DB - FLOOR_DB))
    norm = np.clip(norm, 0.0, 1.0)
    return norm.tolist()


def _refresh_devices():
    """Populate _inputs with available input devices."""
    global _inputs
    try:
        import sounddevice as sd
    except Exception:
        with _lock:
            _inputs = []
        return
    try:
        lst = []
        for i, dev in enumerate(sd.query_devices()):
            if dev.get('max_input_channels', 0) > 0:
                name = dev.get('name', f'Dev {i}')
                host = dev.get('hostapi', None)
                if host is not None:
                    try:
                        hostname = sd.query_hostapis(host).get('name', '')
                        name = f"{name} ({hostname})"
                    except Exception:
                        pass
                lst.append((i, name))
        with _lock:
            _inputs = lst
    except Exception:
        with _lock:
            _inputs = []


def register_raw_tap(page_id):
    """Allow another page to receive raw audio blocks from this stream."""
    try:
        pid = int(page_id)
    except Exception:
        return
    _RAW_PAGE_IDS.add(pid)
    _ACTIVE_PAGE_IDS.add(pid)


def register_spectrum_tap(page_id):
    """Allow another page to keep live spectrum levels updated."""
    try:
        pid = int(page_id)
    except Exception:
        return
    _SPECTRUM_PAGE_IDS.add(pid)
    _ACTIVE_PAGE_IDS.add(pid)


def ensure_background():
    """Ensure the audio thread is running for background consumers."""
    _ensure_thread()


def get_levels():
    """Return a copy of the latest normalized spectrum levels."""
    with _lock:
        return list(_last_levels) if _last_levels else []


def get_last_audio_block():
    """Return (block, seq, sr, ts). block is a float32 mono numpy array copy."""
    with _lock:
        if _last_audio_block is None:
            return None, _last_audio_seq, _sr, _last_audio_ts
        return _last_audio_block.copy(), _last_audio_seq, _sr, _last_audio_ts


def get_device_desc():
    """Return a human-readable input device description."""
    with _lock:
        devs = list(_inputs)
        idx = _device_index
    if not devs:
        return "(no inputs found)"
    if idx is None:
        return "default"
    j = max(0, min(idx, len(devs) - 1))
    return f"{j+1}/{len(devs)} {devs[j][1]}"


def _audio_loop():
    global _ready, _sr, _last_levels, _error_msg
    try:
        import numpy as np
        import sounddevice as sd
    except Exception as e:
        with _lock:
            _ready = False
        return

    try:
        _ready = False
        _stop_event.clear()

        def callback(indata, frames, time_info, status):
            # Use globals for shared state; avoid creating locals that shadow them
            global _last_levels, _gain_db, _smooth, _bins, DISPLAY_SCALE, _auto_adapt
            global _last_bg_spectrum_at
            if _stop_event.is_set():
                raise sd.CallbackStop
            # Skip heavy FFT when the spectrum page is not visible
            current = PAGE_ID
            try:
                import midicrt as _mc
                current = _mc.current_page
                if current not in _ACTIVE_PAGE_IDS:
                    return
            except Exception:
                pass
            try:
                want_raw = current in _RAW_PAGE_IDS
                if want_raw:
                    blk = indata
                    if blk.ndim == 2:
                        blk = blk.mean(axis=1)
                    blk = blk.astype(np.float32, copy=True)
                    with _lock:
                        global _last_audio_block, _last_audio_seq, _last_audio_ts
                        _last_audio_block = blk
                        _last_audio_seq += 1
                        _last_audio_ts = time.time()
                if current not in _SPECTRUM_PAGE_IDS:
                    return
                bins = int(_bins)
                if current != PAGE_ID:
                    now_t = time.time()
                    if (now_t - _last_bg_spectrum_at) < (1.0 / max(1.0, _BG_SPECTRUM_HZ)):
                        return
                    _last_bg_spectrum_at = now_t
                    bins = min(bins, int(_BG_SPECTRUM_BINS))
                # compute spectrum for this block
                levels = _compute_spectrum(indata.copy(), _sr, max(8, bins))
                g = 10 ** (_gain_db / 20.0)
                with _lock:
                    if not _last_levels or len(_last_levels) != len(levels):
                        _last_levels = [0.0] * len(levels)
                    # optional auto adaptation based on current peak (pre-display-scale)
                    if _auto_adapt and levels:
                        peak = max(levels) * g
                        if peak > 1e-4:  # ignore near-silence
                            desired = ADAPT_TARGET / peak
                            # clamp desired scale
                            desired = max(SCALE_MIN, min(SCALE_MAX, desired))
                            # smooth toward desired
                            DISPLAY_SCALE += ADAPT_RATE * (desired - DISPLAY_SCALE)
                    for i, v in enumerate(levels):
                        # apply user gain and display headroom scaling
                        target = min(1.0, max(0.0, v * g * DISPLAY_SCALE))
                        sm = _smooth * _last_levels[i] + (1 - _smooth) * target
                        _last_levels[i] = sm
            except Exception:
                # swallow callback errors to keep stream alive
                pass
        # select device
        dev_arg = None
        with _lock:
            sel = _device_index
        if sel is not None and _inputs:
            # map our list position to real device index
            try:
                dev_arg = _inputs[sel][0]
            except Exception:
                dev_arg = None

        with sd.InputStream(samplerate=_sr, channels=1, dtype='float32', blocksize=BLOCKSIZE, callback=callback, device=dev_arg):
            _ready = True
            _error_msg = None
            while not _stop_event.is_set():
                time.sleep(0.05)
    except Exception as e:
        with _lock:
            _ready = False
        _error_msg = f"Audio init failed: {e}"


def _ensure_thread():
    global _audio_thread, _last_thread_start
    if _audio_thread and _audio_thread.is_alive():
        return
    now = time.time()
    if now - _last_thread_start < _THREAD_COOLDOWN:
        return
    _last_thread_start = now
    if not _try_imports():
        return
    _load_config()
    _refresh_devices()
    _audio_thread = threading.Thread(target=_audio_loop, name="audio-spectrum", daemon=True)
    _audio_thread.start()


def _restart_audio():
    global _audio_thread, _ready
    if _audio_thread and _audio_thread.is_alive():
        _stop_event.set()
        _audio_thread.join(timeout=1.0)
    _ready = False
    _stop_event.clear()
    _audio_thread = None
    _ensure_thread()


def keypress(ch):
    global _bins, _gain_db, _smooth, _device_index, FLOOR_DB, CEIL_DB, DISPLAY_SCALE, _auto_adapt, _freq_scale, _agg_mode, FMIN_HZ, HPF_ON
    s = str(ch)
    if s in ("[", "{"):
        _bins = max(8, _bins - 8)
        _mark_dirty(); return True
    if s in ("]", "}"):
        _bins = min(256, _bins + 8)
        _mark_dirty(); return True
    if s.lower() == "g":
        _gain_db = min(24.0, _gain_db + 3.0)
        _mark_dirty(); return True
    if s.lower() == "h":
        _gain_db = max(-24.0, _gain_db - 3.0)
        _mark_dirty(); return True
    if s.lower() == "s":
        _smooth = min(0.95, _smooth + 0.05)
        _mark_dirty(); return True
    if s.lower() == "a":
        _smooth = max(0.0, _smooth - 0.05)
        _mark_dirty(); return True
    # scale control: adjust dB floor/ceiling
    if s.lower() == "f":
        FLOOR_DB = min(CEIL_DB - 5.0, FLOOR_DB + 5.0)
        _mark_dirty(); return True
    if s.lower() == "v":
        FLOOR_DB = max(-140.0, FLOOR_DB - 5.0)
        _mark_dirty(); return True
    if s.lower() == "c":
        CEIL_DB = min(30.0, CEIL_DB + 5.0)
        _mark_dirty(); return True
    if s.lower() == "x":
        CEIL_DB = max(FLOOR_DB + 10.0, CEIL_DB - 5.0)
        _mark_dirty(); return True
    # quick zoom of dB span around current center
    # removed u/i zoom controls per request
    # display scale (overall headroom)
    if s.lower() == 'j':
        DISPLAY_SCALE = max(0.1, DISPLAY_SCALE - 0.05)
        _mark_dirty(); return True
    if s.lower() == 'k':
        DISPLAY_SCALE = min(1.0, DISPLAY_SCALE + 0.05)
        _mark_dirty(); return True
    if s.lower() == 'z':
        _auto_adapt = not _auto_adapt
        _mark_dirty(); return True
    if s == 'Z':
        DISPLAY_SCALE = 0.85
        _mark_dirty(); return True
    if s.lower() == 'l':
        _freq_scale = 'lin' if _freq_scale == 'log' else 'log'
        _mark_dirty(); return True
    if s.lower() == 'm':
        _agg_mode = 'avg' if _agg_mode == 'max' else 'max'
        _mark_dirty(); return True
    # low-cut (log band start frequency) and HPF toggle
    if s == 'n':
        FMIN_HZ = max(10.0, FMIN_HZ - 10.0)
        _mark_dirty(); return True
    if s == 'N':
        FMIN_HZ = min((_sr / 2.0) - 10.0, FMIN_HZ + 10.0)
        _mark_dirty(); return True
    if s.lower() == 'p':
        HPF_ON = not HPF_ON
        _mark_dirty(); return True
    # device control: ',' previous, '.' next, '0' default, 'r' refresh
    if s == ",":
        with _lock:
            if _inputs:
                if _device_index is None:
                    _device_index = 0
                _device_index = (_device_index - 1) % len(_inputs)
        _restart_audio()
        return True
    if s == ".":
        with _lock:
            if _inputs:
                if _device_index is None:
                    _device_index = 0
                _device_index = (_device_index + 1) % len(_inputs)
        _restart_audio()
        return True
    if s == "0":
        with _lock:
            _device_index = None
        _restart_audio()
        return True
    if s.lower() == "r":
        _refresh_devices()
        return True
    return False


def _bar_rows(height, width, levels):
    """Return a list of text rows representing the current spectrum bars."""
    if height <= 0 or width <= 0:
        return []
    n = min(width, len(levels))
    if n <= 0:
        return [" " * width for _ in range(height)]
    step = len(levels) / n
    rows = [list(" " * width) for _ in range(height)]
    for i in range(n):
        j0 = int(i * step)
        j1 = int((i + 1) * step)
        if j1 <= j0:
            val = levels[min(j0, len(levels) - 1)]
        else:
            val = sum(levels[j0:j1]) / (j1 - j0)
        h = int(val * height + 0.5)
        for r in range(height - 1, height - h - 1, -1):
            if 0 <= r < height:
                rows[r][i] = "█"
    return ["".join(row) for row in rows]


def _draw_bars(y_top, y_bottom, x_left, x_right, levels):
    height = max(1, y_bottom - y_top + 1)
    width = max(1, x_right - x_left + 1)
    rows = _bar_rows(height, width, levels)
    # emit all rows in one write to minimise syscall overhead
    buf = "".join(
        term.move_yx(y_top + idx, x_left) + row
        for idx, row in enumerate(rows)
    )
    sys.stdout.write(buf)


def draw(state):
    cols = state["cols"]
    rows = state["rows"]
    y0 = state.get("y_offset", 3)

    _ensure_thread()

    # Opportunistically persist config (no file I/O in audio callback)
    global _cfg_dirty, _cfg_last_save
    if _cfg_dirty:
        now = time.time()
        # throttle saves to at most ~2 Hz
        if now - _cfg_last_save > 0.5:
            _save_config()
            _cfg_last_save = now
            _cfg_dirty = False

    draw_line(y0, f"--- {PAGE_NAME} ---".ljust(cols))

    if _error_msg:
        draw_line(y0 + 1, _error_msg[:cols])
        draw_line(y0 + 2, "Controls: [ ] bins, g/h gain, s/a smoothing"[:cols])
        return

    # Device display
    with _lock:
        devs = list(_inputs)
        idx = _device_index
    if not devs:
        dev_txt = "(no inputs found)"
    elif idx is None:
        dev_txt = "default"
    else:
        j = max(0, min(idx, len(devs)-1))
        dev_txt = f"{j+1}/{len(devs)} {devs[j][1]}"

    # Split long status across lines for readability
    status1 = (
        f"Input:{'OK' if _ready else '…'}  Dev:{dev_txt}  SR:{_sr}  Bins:{_bins}  "
        f"Gain:{_gain_db:+.1f}dB  Smooth:{_smooth:.2f}"
    )
    status2 = (
        f"dB:{int(FLOOR_DB)}/{int(CEIL_DB)}  Disp:{DISPLAY_SCALE:.2f}  Freq:{_freq_scale}  "
        f"Agg:{_agg_mode}  LC:{int(FMIN_HZ)}Hz  HPF:{'on' if HPF_ON else 'off'}  Auto:{'on' if _auto_adapt else 'off'}"
    )
    draw_line(y0 + 1, status1[:cols])
    draw_line(y0 + 2, status2[:cols])

    # Multi-line help for better readability on narrow terminals
    help1 = "Controls: [ ] bins  g/h gain  s/a smooth  j/k disp  z auto  Z reset"
    help2 = "          f/v floor  c/x ceil  l lin/log  m avg/max  n/N lowcut  p HPF"
    help3 = "          ,/. device  0 default  r refresh"
    draw_line(y0 + 3, help1[:cols])
    draw_line(y0 + 4, help2[:cols])
    draw_line(y0 + 5, help3[:cols])

    # compute drawing region
    top = y0 + 6
    bottom = rows - 5  # leave footer space
    left = 1
    right = cols - 2

    with _lock:
        levels = list(_last_levels) if _last_levels else [0.0] * max(8, min(_bins, cols - 2))

    if str(state.get("render_backend", "")).startswith("compositor"):
        rows_txt = _bar_rows(max(1, bottom - top + 1), max(1, right - left + 1), levels)
        pad = " " * max(0, left)
        for idx, row in enumerate(rows_txt):
            draw_line(top + idx, pad + row)
    else:
        _draw_bars(top, bottom, left, right, levels)


def _build_widget_lines(state):
    cols = int(state.get("cols", 95))
    rows = int(state.get("rows", 30))
    lines = [f"--- {PAGE_NAME} ---"]
    if _error_msg:
        return lines + [_error_msg, "Controls: [ ] bins, g/h gain, s/a smoothing"]
    with _lock:
        devs = list(_inputs)
        idx = _device_index
        levels = list(_last_levels) if _last_levels else [0.0] * max(8, min(_bins, cols - 2))
    if not devs:
        dev_txt = "(no inputs found)"
    elif idx is None:
        dev_txt = "default"
    else:
        j = max(0, min(idx, len(devs)-1)); dev_txt = f"{j+1}/{len(devs)} {devs[j][1]}"
    lines.append(f"Input:{'OK' if _ready else '…'}  Dev:{dev_txt}  SR:{_sr}  Bins:{_bins}  Gain:{_gain_db:+.1f}dB  Smooth:{_smooth:.2f}")
    lines.append(f"dB:{int(FLOOR_DB)}/{int(CEIL_DB)}  Disp:{DISPLAY_SCALE:.2f}  Freq:{_freq_scale}  Agg:{_agg_mode}  LC:{int(FMIN_HZ)}Hz  HPF:{'on' if HPF_ON else 'off'}  Auto:{'on' if _auto_adapt else 'off'}")
    lines.extend(["Controls: [ ] bins  g/h gain  s/a smooth  j/k disp  z auto  Z reset", "          f/v floor  c/x ceil  l lin/log  m avg/max  n/N lowcut  p HPF", "          ,/. device  0 default  r refresh"])
    height = max(1, rows - 11)
    width = max(1, cols - 3)
    rows_txt = _bar_rows(height, width, levels)
    lines.extend((" " + r) for r in rows_txt)
    return lines


def build_widget(state):
    return build_widget_from_legacy_contract(draw, state, draw_line)
