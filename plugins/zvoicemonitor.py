# -*- coding: utf-8 -*-
# Plugin: Voice/Polyphony monitor (prototype)

from collections import deque
import time
import midicrt
from configutil import load_section, save_section

BACKGROUND = True

POLY_LIMIT_GLOBAL = 16
POLY_LIMIT_CH = 8
PER_CH_LIMITS = [POLY_LIMIT_CH] * 16
EVENT_LOG_LEN = 8
OVER_LIMIT_BEATS = 2.0

_cfg = load_section("voice_monitor")
if _cfg is None:
    _cfg = {}
try:
    POLY_LIMIT_GLOBAL = int(_cfg.get("poly_limit_global", POLY_LIMIT_GLOBAL))
    POLY_LIMIT_CH = int(_cfg.get("poly_limit_ch", POLY_LIMIT_CH))
    EVENT_LOG_LEN = int(_cfg.get("event_log_len", EVENT_LOG_LEN))
    OVER_LIMIT_BEATS = float(_cfg.get("over_limit_beats", OVER_LIMIT_BEATS))
    limits = _cfg.get("per_channel_limits", None)
    if isinstance(limits, list) and limits:
        tmp = []
        for v in limits[:16]:
            try:
                tmp.append(int(v))
            except Exception:
                tmp.append(POLY_LIMIT_CH)
        while len(tmp) < 16:
            tmp.append(POLY_LIMIT_CH)
        PER_CH_LIMITS = tmp
    else:
        PER_CH_LIMITS = [POLY_LIMIT_CH] * 16
except Exception:
    pass
try:
    save_section("voice_monitor", {
        "poly_limit_global": int(POLY_LIMIT_GLOBAL),
        "poly_limit_ch": int(POLY_LIMIT_CH),
        "event_log_len": int(EVENT_LOG_LEN),
        "per_channel_limits": list(PER_CH_LIMITS),
        "over_limit_beats": float(OVER_LIMIT_BEATS),
    })
except Exception:
    pass

_active = {}  # (ch, note) -> count
_active_ch = {ch: 0 for ch in range(1, 17)}
_total_active = 0
_peak_total = 0
_peak_ch = {ch: 0 for ch in range(1, 17)}
_events = deque(maxlen=EVENT_LOG_LEN)  # (ts, ch, note, total, ch_total, ch_limit, hit_global, hit_ch, tag)
_over_start_tick = {ch: None for ch in range(1, 17)}
_over_warned = {ch: False for ch in range(1, 17)}


def _limit_for(ch):
    try:
        lim = PER_CH_LIMITS[ch - 1]
    except Exception:
        lim = POLY_LIMIT_CH
    return lim


def _note_on(ch, note):
    global _total_active, _peak_total
    key = (ch, note)
    _active[key] = _active.get(key, 0) + 1
    _active_ch[ch] = _active_ch.get(ch, 0) + 1
    _total_active += 1
    _peak_total = max(_peak_total, _total_active)
    _peak_ch[ch] = max(_peak_ch.get(ch, 0), _active_ch[ch])
    ch_limit = _limit_for(ch)
    hit_global = _total_active > POLY_LIMIT_GLOBAL if POLY_LIMIT_GLOBAL > 0 else False
    hit_ch = (_active_ch[ch] > ch_limit) if (ch_limit and ch_limit > 0) else False
    if hit_global or hit_ch:
        _events.appendleft((time.time(), ch, note, _total_active, _active_ch[ch], ch_limit, hit_global, hit_ch, "instant"))


def _note_off(ch, note):
    global _total_active
    key = (ch, note)
    if key not in _active:
        return
    _active[key] -= 1
    _active_ch[ch] = max(0, _active_ch.get(ch, 0) - 1)
    _total_active = max(0, _total_active - 1)
    if _active[key] <= 0:
        _active.pop(key, None)


def _clear_channel(ch):
    global _total_active
    for key in [k for k in _active.keys() if k[0] == ch]:
        cnt = _active.get(key, 0)
        _total_active = max(0, _total_active - cnt)
        _active.pop(key, None)
    _active_ch[ch] = 0
    _over_start_tick[ch] = None
    _over_warned[ch] = False


def _update_over(ch, tick):
    ch_limit = _limit_for(ch)
    if ch_limit <= 0:
        _over_start_tick[ch] = None
        _over_warned[ch] = False
        return
    if _active_ch.get(ch, 0) > ch_limit:
        if _over_start_tick[ch] is None:
            _over_start_tick[ch] = tick
        if not _over_warned[ch]:
            if tick - _over_start_tick[ch] >= int(OVER_LIMIT_BEATS * 24):
                _over_warned[ch] = True
                _events.appendleft((time.time(), ch, None, _total_active, _active_ch[ch], ch_limit, False, True, "sustain"))
    else:
        _over_start_tick[ch] = None
        _over_warned[ch] = False


def handle(msg):
    if msg.type == "note_on":
        ch = msg.channel + 1
        if msg.velocity == 0:
            _note_off(ch, msg.note)
        else:
            _note_on(ch, msg.note)
    elif msg.type == "note_off":
        ch = msg.channel + 1
        _note_off(ch, msg.note)
    elif msg.type == "control_change":
        ch = msg.channel + 1
        if msg.control in (120, 123):
            _clear_channel(ch)


def get_voice_stats():
    return {
        "total": _total_active,
        "peak_total": _peak_total,
        "per_ch": dict(_active_ch),
        "peak_ch": dict(_peak_ch),
        "events": list(_events),
        "per_ch_limits": list(PER_CH_LIMITS),
        "over_warned": dict(_over_warned),
    }


def draw(state):
    # use clock ticks to detect sustained over-limit
    try:
        tick = int(state.get("tick", 0))
    except Exception:
        tick = 0
    for ch in range(1, 17):
        _update_over(ch, tick)
