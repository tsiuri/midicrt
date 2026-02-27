# pages/tuner.py — Audio tuner (aubio)
BACKGROUND = True
PAGE_ID = 10
PAGE_NAME = "Tuner"

import math
import time
import sys
from blessed import Terminal
from midicrt import draw_line
from configutil import load_section, save_section
from pages.legacy_contract_bridge import build_widget_from_contract

audio = sys.modules.get("pages.audiospectrum")
if audio is None:
    try:
        import importlib
        audio = importlib.import_module("pages.audiospectrum")
    except Exception:
        audio = None

term = Terminal()

PITCH_METHOD = "yin"
TOLERANCE = 0.8
SILENCE_DB = -55.0
MIN_CONF = 0.30
SMOOTHING = 0.55

_cfg = load_section("tuner")
if _cfg is None:
    _cfg = {}
try:
    PITCH_METHOD = str(_cfg.get("pitch_method", PITCH_METHOD))
    TOLERANCE = float(_cfg.get("tolerance", TOLERANCE))
    SILENCE_DB = float(_cfg.get("silence_db", SILENCE_DB))
    MIN_CONF = float(_cfg.get("min_conf", MIN_CONF))
    SMOOTHING = float(_cfg.get("smoothing", SMOOTHING))
except Exception:
    pass

try:
    save_section("tuner", {
        "pitch_method": str(PITCH_METHOD),
        "tolerance": float(TOLERANCE),
        "silence_db": float(SILENCE_DB),
        "min_conf": float(MIN_CONF),
        "smoothing": float(SMOOTHING),
    })
except Exception:
    pass

_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

_pitch_obj = None
_pitch_sr = None
_pitch_hop = None
_last_seq = -1
_last_hz = 0.0
_last_conf = 0.0
_last_db = -120.0
_smoothed_hz = None
_last_note = ""
_last_cents = 0.0
_last_update = 0.0
_error_msg = None

# Ensure the audio stream remains active on this page
if audio is not None:
    audio.register_raw_tap(PAGE_ID)


def _ensure_pitch(sr, hop):
    global _pitch_obj, _pitch_sr, _pitch_hop, _error_msg
    if _pitch_obj and _pitch_sr == sr and _pitch_hop == hop:
        return True
    try:
        from aubio import pitch as aubio_pitch
    except Exception as e:
        _pitch_obj = None
        _error_msg = f"Missing deps: {e}. Install: pip install aubio"
        return False
    win = max(2048, int(hop) * 4)
    p = aubio_pitch(PITCH_METHOD, win, int(hop), int(sr))
    p.set_unit("Hz")
    p.set_tolerance(float(TOLERANCE))
    try:
        p.set_silence(float(SILENCE_DB))
    except Exception:
        pass
    _pitch_obj = p
    _pitch_sr = sr
    _pitch_hop = hop
    _error_msg = None
    return True


def _freq_to_note(freq):
    if freq <= 0.0:
        return "", 0.0
    midi = 69 + 12 * math.log2(freq / 440.0)
    nearest = int(round(midi))
    note = _NOTE_NAMES[nearest % 12]
    octave = nearest // 12 - 1
    note_name = f"{note}{octave}"
    nearest_freq = 440.0 * (2 ** ((nearest - 69) / 12))
    cents = 1200.0 * math.log2(freq / nearest_freq) if nearest_freq > 0 else 0.0
    return note_name, cents


def _meter(cents, width):
    width = max(7, int(width))
    center = width // 2
    pos = center + int(round((cents / 50.0) * center))
    pos = max(0, min(width - 1, pos))
    chars = ["-"] * width
    chars[center] = "|"
    chars[pos] = "^"
    return "".join(chars)


def draw(state):
    global _last_seq, _last_hz, _last_conf, _last_db, _smoothed_hz
    global _last_note, _last_cents, _last_update

    cols = state["cols"]
    y0 = state.get("y_offset", 3)

    if audio is None:
        draw_line(y0, f"--- {PAGE_NAME} ---".ljust(cols))
        draw_line(y0 + 1, "Audio page not available."[:cols])
        return

    audio._ensure_thread()

    block, seq, sr, ts = audio.get_last_audio_block()
    if block is not None and seq != _last_seq:
        _last_seq = seq
        if _ensure_pitch(sr, len(block)):
            try:
                import numpy as np
                x = block.astype(np.float32, copy=False)
                if x.size:
                    rms = math.sqrt(float(np.mean(x * x)))
                    _last_db = 20.0 * math.log10(rms + 1e-12)
                else:
                    _last_db = -120.0
                hz = float(_pitch_obj(x))
                _last_conf = float(_pitch_obj.get_confidence())
                _last_hz = hz
                _last_update = time.time()
                if hz > 0.0 and _last_conf >= MIN_CONF and _last_db >= SILENCE_DB:
                    if _smoothed_hz is None:
                        _smoothed_hz = hz
                    else:
                        _smoothed_hz = (SMOOTHING * _smoothed_hz) + ((1.0 - SMOOTHING) * hz)
                    _last_note, _last_cents = _freq_to_note(_smoothed_hz)
                else:
                    _smoothed_hz = None
                    _last_note = ""
                    _last_cents = 0.0
            except Exception as e:
                global _error_msg
                _error_msg = f"Pitch error: {e}"

    draw_line(y0, f"--- {PAGE_NAME} ---".ljust(cols))

    if _error_msg:
        draw_line(y0 + 1, _error_msg[:cols])
        return

    dev_desc = audio.get_device_desc()
    status = f"Input:{'OK' if block is not None else '…'}  Dev:{dev_desc}  SR:{sr}"
    draw_line(y0 + 1, status[:cols])

    if _last_note:
        line = (
            f"Note:{_last_note:<4}  Pitch:{_smoothed_hz:7.2f} Hz  "
            f"Cents:{_last_cents:+6.1f}  Conf:{_last_conf:.2f}  Level:{_last_db:5.1f} dB"
        )
        draw_line(y0 + 2, line[:cols])
        meter = _meter(_last_cents, cols - 9)
        draw_line(y0 + 3, ("Tuning: " + meter)[:cols])
    else:
        line = f"Listening...  Conf:{_last_conf:.2f}  Level:{_last_db:5.1f} dB"
        draw_line(y0 + 2, line[:cols])
        draw_line(y0 + 3, "".ljust(cols))


def build_widget(state):
    return build_widget_from_contract(draw, state, draw_line)
