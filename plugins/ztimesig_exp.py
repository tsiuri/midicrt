# -*- coding: utf-8 -*-
# Plugin: Experimental time signature inference (meter DBN-ish)

import math
import time
from collections import deque
import midicrt
from configutil import load_section, save_section

BACKGROUND = True

PPQN = 24
CANDIDATES = [
    ("2/4", 2, 4),
    ("3/4", 3, 4),
    ("4/4", 4, 4),
    ("5/4", 5, 4),
    ("7/4", 7, 4),
    ("6/8", 6, 8),
    ("7/8", 7, 8),
    ("9/8", 9, 8),
    ("12/8", 12, 8),
]

WINDOW_TICKS = 768
MIN_EVENTS = 12
SIGMA_BAR = 6.0
SIGMA_BEAT = 3.0
SIGMA_SUB = 2.0
WEIGHT_DOWN = 1.0
WEIGHT_BEAT = 0.6
WEIGHT_SUB = 0.3
OFF_PENALTY = 0.25
EVAL_INTERVAL = 0.5
MIN_CONF = 0.35
CHANGE_CONFIRM = 3
COLLAPSE_SAME_TICK = True
DEFAULT_PRIOR = {
    "2/4": 1.0,
    "3/4": 1.1,
    "4/4": 1.2,
    "5/4": 0.9,
    "7/4": 0.8,
    "6/8": 1.0,
    "7/8": 0.8,
    "9/8": 0.9,
    "12/8": 1.0,
}

_cfg = load_section("timesig_exp")
if _cfg is None:
    _cfg = {}
try:
    WINDOW_TICKS = int(_cfg.get("window_ticks", WINDOW_TICKS))
    MIN_EVENTS = int(_cfg.get("min_events", MIN_EVENTS))
    SIGMA_BAR = float(_cfg.get("sigma_bar", SIGMA_BAR))
    SIGMA_BEAT = float(_cfg.get("sigma_beat", SIGMA_BEAT))
    SIGMA_SUB = float(_cfg.get("sigma_sub", SIGMA_SUB))
    WEIGHT_DOWN = float(_cfg.get("weight_down", WEIGHT_DOWN))
    WEIGHT_BEAT = float(_cfg.get("weight_beat", WEIGHT_BEAT))
    WEIGHT_SUB = float(_cfg.get("weight_sub", WEIGHT_SUB))
    OFF_PENALTY = float(_cfg.get("off_penalty", OFF_PENALTY))
    EVAL_INTERVAL = float(_cfg.get("eval_interval", EVAL_INTERVAL))
    MIN_CONF = float(_cfg.get("min_conf", MIN_CONF))
    CHANGE_CONFIRM = int(_cfg.get("change_confirm", CHANGE_CONFIRM))
    COLLAPSE_SAME_TICK = bool(_cfg.get("collapse_same_tick", COLLAPSE_SAME_TICK))
    prior = _cfg.get("prior", None)
    if isinstance(prior, dict):
        DEFAULT_PRIOR.update({str(k): float(v) for k, v in prior.items()})
except Exception:
    pass

try:
    save_section("timesig_exp", {
        "window_ticks": int(WINDOW_TICKS),
        "min_events": int(MIN_EVENTS),
        "sigma_bar": float(SIGMA_BAR),
        "sigma_beat": float(SIGMA_BEAT),
        "sigma_sub": float(SIGMA_SUB),
        "weight_down": float(WEIGHT_DOWN),
        "weight_beat": float(WEIGHT_BEAT),
        "weight_sub": float(WEIGHT_SUB),
        "off_penalty": float(OFF_PENALTY),
        "eval_interval": float(EVAL_INTERVAL),
        "min_conf": float(MIN_CONF),
        "change_confirm": int(CHANGE_CONFIRM),
        "collapse_same_tick": bool(COLLAPSE_SAME_TICK),
        "prior": dict(DEFAULT_PRIOR),
    })
except Exception:
    pass

_events = deque()  # (tick, weight)
_last_eval = 0.0
_last_result = None  # list of (label, score)
_locked = None  # (label_list, score)
_pending = None  # (label_list, count)
_last_running = False
_total_events = 0
_last_tick_seen = None


def _gauss(d, sigma):
    if sigma <= 0:
        return 0.0
    return math.exp(-(d * d) / (2.0 * sigma * sigma))


def _score_candidate(events, bar_ticks, beat_ticks, sub_ticks=None):
    if not events:
        return 0.0
    total_w = sum(w for _t, w in events) + 1e-6
    s_down = 0.0
    s_beat = 0.0
    s_sub = 0.0
    for tick, w in events:
        phase = tick % bar_ticks
        d_bar = min(phase, bar_ticks - phase)
        d_beat = phase % beat_ticks
        d_beat = min(d_beat, beat_ticks - d_beat)
        s_down += w * _gauss(d_bar, SIGMA_BAR)
        s_beat += w * _gauss(d_beat, SIGMA_BEAT)
        if sub_ticks and sub_ticks > 0:
            d_sub = phase % sub_ticks
            d_sub = min(d_sub, sub_ticks - d_sub)
            s_sub += w * _gauss(d_sub, SIGMA_SUB)
    score = (WEIGHT_DOWN * s_down + WEIGHT_BEAT * s_beat) / total_w
    if sub_ticks and sub_ticks > 0:
        score += WEIGHT_SUB * (s_sub / total_w)
    off = max(0.0, 1.0 - (s_beat / total_w))
    score -= OFF_PENALTY * off
    return score


def _estimate():
    if len(_events) < MIN_EVENTS:
        return None
    events = list(_events)
    scores = []
    for label, beats, denom in CANDIDATES:
        beat_ticks = int(PPQN * (4.0 / denom))
        bar_ticks = beat_ticks * beats
        sub_ticks = None
        if denom == 4:
            sub_ticks = max(1, beat_ticks // 2)
        score = _score_candidate(events, bar_ticks, beat_ticks, sub_ticks=sub_ticks)
        score *= DEFAULT_PRIOR.get(label, 1.0)
        scores.append((label, score))
    scores.sort(key=lambda x: x[1], reverse=True)
    best_label, best_score = scores[0]
    ties = [label for label, sc in scores if (best_score - sc) <= (best_score * 0.02)]
    if len(ties) > 3:
        return None
    return (ties, best_score, scores[:3])


def handle(msg):
    global _last_eval, _last_result, _locked, _pending, _last_running, _total_events
    if msg.type == "start":
        _events.clear()
        _last_result = None
        _locked = None
        _pending = None
        _last_eval = 0.0
        _total_events = 0
        _last_running = True
        _last_tick_seen = None
        return
    if msg.type == "stop":
        _last_running = False
        return
    if msg.type == "note_on" and msg.velocity > 0:
        if not getattr(midicrt, "running", False):
            return
        tick = getattr(midicrt, "tick_counter", 0)
        if tick is None:
            return
        w = 1.0 + (msg.velocity / 127.0)
        if COLLAPSE_SAME_TICK and _events and _events[-1][0] == int(tick):
            prev_tick, prev_w = _events[-1]
            _events[-1] = (prev_tick, max(prev_w, float(w)))
        else:
            _events.append((int(tick), float(w)))
            _total_events += 1
        _last_tick_seen = int(tick)
        if WINDOW_TICKS > 0:
            min_tick = tick - WINDOW_TICKS
            while _events and _events[0][0] < min_tick:
                _events.popleft()
        now = time.time()
        if now - _last_eval > EVAL_INTERVAL:
            est = _estimate()
            _last_result = est
            if est is None:
                if len(_events) < MIN_EVENTS:
                    _locked = None
                    _pending = None
            else:
                labels, score, _top = est
                if score < MIN_CONF:
                    _pending = None
                elif _locked is None:
                    _locked = (labels, score)
                else:
                    locked_labels, _locked_score = _locked
                    if labels == locked_labels:
                        _locked = (labels, score)
                        _pending = None
                    else:
                        if _pending and _pending[0] == labels:
                            _pending = (labels, _pending[1] + 1)
                        else:
                            _pending = (labels, 1)
                        if _pending[1] >= CHANGE_CONFIRM:
                            _locked = (labels, score)
                            _pending = None
            _last_eval = now


def draw(state):
    global _last_running, _last_result, _locked, _pending, _last_eval, _total_events
    running = bool(state.get("running", False))
    if running != _last_running:
        if running:
            _events.clear()
            _last_result = None
            _locked = None
            _pending = None
            _last_eval = 0.0
            _total_events = 0
        _last_running = running


def get_timesig_exp():
    if _locked is None and _last_result is None:
        return None
    if _locked is not None:
        labels, conf = _locked
        top = None
    else:
        labels, conf, top = _last_result
    return {
        "labels": labels,
        "confidence": conf,
        "events": len(_events),
        "events_total": _total_events,
        "pending": _pending[0] if _pending else None,
        "top": top,
    }
