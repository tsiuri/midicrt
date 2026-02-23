# -*- coding: utf-8 -*-
# Plugin: Time signature inference (heuristic)

import math
import time
from collections import deque
import midicrt
from configutil import load_section, save_section

BACKGROUND = True

PPQN = 24
DEFAULT_CANDIDATES = [
    ("2/4",  2 * PPQN,  PPQN,  PPQN // 2),
    ("3/4",  3 * PPQN,  PPQN,  PPQN // 2),
    ("4/4",  4 * PPQN,  PPQN,  PPQN // 2),
    ("5/4",  5 * PPQN,  PPQN,  PPQN // 2),
    ("7/4",  7 * PPQN,  PPQN,  PPQN // 2),
    ("6/8",  6 * (PPQN // 2),  3 * (PPQN // 2),  (PPQN // 2)),
    ("7/8",  7 * (PPQN // 2),  (PPQN // 2),     (PPQN // 2)),
    ("9/8",  9 * (PPQN // 2),  3 * (PPQN // 2),  (PPQN // 2)),
    ("12/8", 12 * (PPQN // 2), 3 * (PPQN // 2),  (PPQN // 2)),
]

MAX_EVENTS = 256
MIN_EVENTS = 12
SIGMA_TICKS = 4.0
TIE_THRESH = 0.02
WINDOW_SECONDS = 20.0
DECAY_SECONDS = 15.0
EVAL_INTERVAL = 0.5
MIN_CONF = 0.35
CHANGE_CONFIRM = 3
COLLAPSE_SAME_TICK = True

_cfg = load_section("timesig")
if _cfg is None:
    _cfg = {}
try:
    MAX_EVENTS = int(_cfg.get("max_events", MAX_EVENTS))
    MIN_EVENTS = int(_cfg.get("min_events", MIN_EVENTS))
    SIGMA_TICKS = float(_cfg.get("sigma_ticks", SIGMA_TICKS))
    TIE_THRESH = float(_cfg.get("tie_thresh", TIE_THRESH))
    WINDOW_SECONDS = float(_cfg.get("window_seconds", WINDOW_SECONDS))
    DECAY_SECONDS = float(_cfg.get("decay_seconds", DECAY_SECONDS))
    EVAL_INTERVAL = float(_cfg.get("eval_interval", EVAL_INTERVAL))
    MIN_CONF = float(_cfg.get("min_conf", MIN_CONF))
    CHANGE_CONFIRM = int(_cfg.get("change_confirm", CHANGE_CONFIRM))
    COLLAPSE_SAME_TICK = bool(_cfg.get("collapse_same_tick", COLLAPSE_SAME_TICK))
except Exception:
    pass

try:
    save_section("timesig", {
        "max_events": int(MAX_EVENTS),
        "min_events": int(MIN_EVENTS),
        "sigma_ticks": float(SIGMA_TICKS),
        "tie_thresh": float(TIE_THRESH),
        "window_seconds": float(WINDOW_SECONDS),
        "decay_seconds": float(DECAY_SECONDS),
        "eval_interval": float(EVAL_INTERVAL),
        "min_conf": float(MIN_CONF),
        "change_confirm": int(CHANGE_CONFIRM),
        "collapse_same_tick": bool(COLLAPSE_SAME_TICK),
    })
except Exception:
    pass

_events = deque()  # list of (tick, ts, weight)
_last_result = None  # (labels, confidence)
_last_eval = 0.0
_locked = None  # (labels, confidence)
_pending = None  # (labels, count)
_last_cleanup = 0.0
_total_events = 0
_last_window_count = 0
_last_running = False
_last_tick_seen = None
_last_tick_ts = 0.0


def _gauss(d, sigma):
    if sigma <= 0:
        return 0.0
    return math.exp(-(d * d) / (2.0 * sigma * sigma))


def _score_candidate(events, bar_ticks, beat_ticks, step_ticks, sigma):
    if not events:
        return 0.0
    offsets = range(0, bar_ticks, max(1, step_ticks))
    best = 0.0
    total_w = sum(w for _t, w in events) + 1e-6
    for off in offsets:
        score = 0.0
        for tick, w in events:
            phase = (tick - off) % bar_ticks
            d_bar = min(phase, bar_ticks - phase)
            d_beat = phase % beat_ticks
            d_beat = min(d_beat, beat_ticks - d_beat)
            s = w * (1.0 * _gauss(d_bar, sigma) + 0.6 * _gauss(d_beat, sigma))
            if step_ticks < beat_ticks:
                d_sub = phase % step_ticks
                d_sub = min(d_sub, step_ticks - d_sub)
                s += w * 0.25 * _gauss(d_sub, sigma)
            score += s
        best = max(best, score)
    return best / total_w


def _estimate(now):
    global _last_window_count
    if len(_events) < MIN_EVENTS:
        _last_window_count = len(_events)
        return None
    # apply time window + decay
    events = []
    for tick, ts, w in _events:
        if WINDOW_SECONDS > 0 and (now - ts) > WINDOW_SECONDS:
            continue
        if DECAY_SECONDS > 0:
            dw = w * math.exp(-(now - ts) / DECAY_SECONDS)
            if dw <= 1e-6:
                continue
            events.append((tick, dw))
        else:
            events.append((tick, w))
    _last_window_count = len(events)
    if len(events) < MIN_EVENTS:
        return None
    scores = []
    for label, bar_ticks, beat_ticks, step_ticks in DEFAULT_CANDIDATES:
        score = _score_candidate(events, bar_ticks, beat_ticks, step_ticks, SIGMA_TICKS)
        scores.append((label, score))
    scores.sort(key=lambda x: x[1], reverse=True)
    best_label, best_score = scores[0]
    ties = [label for label, sc in scores if (best_score - sc) <= (best_score * TIE_THRESH)]
    if len(ties) > 3:
        return None
    return (ties, best_score)


def handle(msg):
    global _last_result, _last_eval, _locked, _pending, _last_cleanup, _total_events
    global _last_running, _last_tick_seen, _last_tick_ts
    if msg.type == "start":
        _events.clear()
        _last_result = None
        _last_eval = 0.0
        _locked = None
        _pending = None
        _total_events = 0
        _last_running = True
        _last_tick_seen = None
        _last_tick_ts = 0.0
        return
    if msg.type == "stop":
        # retain last known signature until next start
        _last_running = False
        return
    if msg.type == "note_on" and msg.velocity > 0:
        if not getattr(midicrt, "running", False):
            return
        tick = getattr(midicrt, "tick_counter", 0)
        if tick is None:
            return
        w = 1.0 + (msg.velocity / 127.0)
        now = time.time()
        if COLLAPSE_SAME_TICK and _events and _events[-1][0] == int(tick):
            # merge chords on the same tick into a single onset
            prev_tick, prev_ts, prev_w = _events[-1]
            _events[-1] = (prev_tick, prev_ts, max(prev_w, float(w)))
        else:
            _events.append((int(tick), now, float(w)))
            _total_events += 1
        _last_tick_seen = int(tick)
        _last_tick_ts = now
        if WINDOW_SECONDS > 0:
            while _events and (now - _events[0][1]) > WINDOW_SECONDS:
                _events.popleft()
        elif MAX_EVENTS and MAX_EVENTS > 0:
            while len(_events) > MAX_EVENTS:
                _events.popleft()
        if now - _last_cleanup > 1.0:
            _last_cleanup = now
            # purge out-of-window events even if no new notes arrive
            if WINDOW_SECONDS > 0:
                while _events and (now - _events[0][1]) > WINDOW_SECONDS:
                    _events.popleft()
        if now - _last_eval > EVAL_INTERVAL:
            est = _estimate(now)
            _last_result = est
            if est is None:
                if len(_events) < MIN_EVENTS:
                    _locked = None
                    _pending = None
            else:
                labels, score = est
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


def get_timesig():
    if _locked is None and _last_result is None:
        return None
    if _locked is not None:
        labels, conf = _locked
    else:
        labels, conf = _last_result
    return {
        "labels": labels,
        "confidence": conf,
        "events": len(_events),
        "events_window": _last_window_count,
        "events_total": _total_events,
        "pending": _pending[0] if _pending else None,
    }


def draw(state):
    """Reset when transport stops/starts, even if we don't receive stop/start."""
    global _last_running, _last_result, _last_eval, _locked, _pending, _total_events
    running = bool(state.get("running", False))
    if running != _last_running:
        if running:
            # reset on (re)start
            _events.clear()
            _last_result = None
            _last_eval = 0.0
            _locked = None
            _pending = None
            _total_events = 0
        # on stop, keep last known signature
        _last_running = running
