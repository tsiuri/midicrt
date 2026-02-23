# -*- coding: utf-8 -*-
# Plugin: Harmony detector (chords + scales)

from collections import deque
import sys
import time
from harmony import detect_harmony, detect_harmony_info
from configutil import load_section, save_section
import midicrt

BACKGROUND = True

RECENT_NOTE_COUNT = 24
RECENT_NOTE_SECONDS = 0.0  # set >0 to enable time-based pruning
MIN_UNIQUE_FOR_CHORD = 2
MIN_UNIQUE_FOR_SCALE = 3
CHORD_MIN_RATIO = 0.6
SCALE_MIN_RATIO = 0.7
KEY_HISTORY_LEN = 256

_cfg = load_section("harmony")
if _cfg is None:
    _cfg = {}
try:
    RECENT_NOTE_COUNT = int(_cfg.get("recent_note_count", RECENT_NOTE_COUNT))
    RECENT_NOTE_SECONDS = float(_cfg.get("recent_note_seconds", RECENT_NOTE_SECONDS))
    MIN_UNIQUE_FOR_CHORD = int(_cfg.get("min_unique_for_chord", MIN_UNIQUE_FOR_CHORD))
    MIN_UNIQUE_FOR_SCALE = int(_cfg.get("min_unique_for_scale", MIN_UNIQUE_FOR_SCALE))
    CHORD_MIN_RATIO = float(_cfg.get("chord_min_ratio", CHORD_MIN_RATIO))
    SCALE_MIN_RATIO = float(_cfg.get("scale_min_ratio", SCALE_MIN_RATIO))
    KEY_HISTORY_LEN = int(_cfg.get("key_history_len", KEY_HISTORY_LEN))
except Exception:
    pass

try:
    save_section("harmony", {
        "recent_note_count": int(RECENT_NOTE_COUNT),
        "recent_note_seconds": float(RECENT_NOTE_SECONDS),
        "min_unique_for_chord": int(MIN_UNIQUE_FOR_CHORD),
        "min_unique_for_scale": int(MIN_UNIQUE_FOR_SCALE),
        "chord_min_ratio": float(CHORD_MIN_RATIO),
        "scale_min_ratio": float(SCALE_MIN_RATIO),
        "key_history_len": int(KEY_HISTORY_LEN),
    })
except Exception:
    pass

_recent_notes = deque(maxlen=RECENT_NOTE_COUNT)  # (note, ts)
_last_pcs = None
_last_result = (None, None)
_last_scale_pcs = set()
_last_note = None
_last_chord_label = None
_last_scale_label = None
_last_chord_info = None
_last_scale_info = None
_key_notes = deque(maxlen=KEY_HISTORY_LEN)  # pitch classes only
_key_counts = [0] * 12

_chord_change_times = deque(maxlen=16)   # timestamps of chord label changes
_last_note_for_iv = None                 # previous note for interval computation
_interval_history = deque(maxlen=64)     # signed semitone intervals, newest first

CHORD_HISTORY = deque(maxlen=4)
SCALE_HISTORY = deque(maxlen=4)  # list of dicts: {label, pcs, in, total, uniq_in, uniq_total}

_scale_current_label = None

# Ensure we are registered as a plugin even if imported elsewhere
try:
    if not any(m.__name__ == __name__ for m in getattr(midicrt, "PLUGINS", [])):
        midicrt.PLUGINS.append(sys.modules[__name__])
except Exception:
    pass


def handle(msg):
    if msg.type == "note_on" and msg.velocity > 0:
        _recent_notes.append((msg.note, time.time()))
        global _last_note, _last_note_for_iv
        if _last_note_for_iv is not None:
            _interval_history.appendleft(msg.note - _last_note_for_iv)
        _last_note = msg.note
        _last_note_for_iv = msg.note
        pc = msg.note % 12
        if _key_notes.maxlen is not None and len(_key_notes) >= _key_notes.maxlen:
            try:
                old = _key_notes[0]
                _key_counts[old] = max(0, _key_counts[old] - 1)
            except Exception:
                pass
        _key_notes.append(pc)
        _key_counts[pc] += 1
        if SCALE_HISTORY:
            for item in SCALE_HISTORY:
                if not isinstance(item, dict):
                    continue
                item["total"] += 1
                if pc in item["pcs"]:
                    item["in"] += 1
                item["uniq_total"].add(pc)
                if pc in item["pcs"]:
                    item["uniq_in"].add(pc)
        # invalidate cache
        global _last_pcs
        _last_pcs = None


def get_harmony():
    global _last_pcs, _last_result, _last_scale_pcs
    global _last_chord_label, _last_scale_label
    global _last_chord_info, _last_scale_info
    if not _recent_notes:
        return (None, None)
    if RECENT_NOTE_SECONDS > 0:
        now = time.time()
        changed = False
        while _recent_notes and (now - _recent_notes[0][1]) > RECENT_NOTE_SECONDS:
            _recent_notes.popleft()
            changed = True
        if changed:
            _last_pcs = None
        if not _recent_notes:
            return (None, None)
    pcs = tuple(sorted({n % 12 for n, _ts in _recent_notes}))
    if pcs != _last_pcs:
        _last_pcs = pcs
        chord, scale = detect_harmony_info(
            pcs,
            min_chord_notes=MIN_UNIQUE_FOR_CHORD,
            min_scale_notes=MIN_UNIQUE_FOR_SCALE,
            chord_min_ratio=CHORD_MIN_RATIO,
            scale_min_ratio=SCALE_MIN_RATIO,
        )
        def _label(info):
            if not info:
                return None
            if isinstance(info, list):
                return " / ".join(i["label"] for i in info)
            return info["label"]
        chord_label = _label(chord)
        scale_label = _label(scale)
        _last_result = (chord_label, scale_label)
        _last_chord_info = chord
        _last_scale_info = scale
        if isinstance(scale, list):
            pcs_list = [set(i["pcs"]) for i in scale if isinstance(i, dict)]
            if pcs_list:
                _last_scale_pcs = set.intersection(*pcs_list)
            else:
                _last_scale_pcs = set()
        else:
            _last_scale_pcs = set(scale["pcs"]) if scale else set()
        global _scale_current_label
        if scale_label and scale_label != _scale_current_label:
            _scale_current_label = scale_label
            # remove duplicate labels in history
            if SCALE_HISTORY:
                tmp = deque(
                    [item for item in SCALE_HISTORY
                     if isinstance(item, dict) and item.get("label") != scale_label],
                    maxlen=4,
                )
                SCALE_HISTORY.clear()
                SCALE_HISTORY.extend(tmp)
            # guard against leftover non-dict entries from older versions
            if SCALE_HISTORY:
                tmp2 = deque([item for item in SCALE_HISTORY if isinstance(item, dict)], maxlen=4)
                SCALE_HISTORY.clear()
                SCALE_HISTORY.extend(tmp2)
            SCALE_HISTORY.appendleft({
                "label": scale_label,
                "pcs": set(_last_scale_pcs),
                "in": 0,
                "total": 0,
                "uniq_in": set(),
                "uniq_total": set(),
            })
        if chord_label and chord_label != _last_chord_label:
            if not CHORD_HISTORY or CHORD_HISTORY[0] != chord_label:
                CHORD_HISTORY.appendleft(chord_label)
            _last_chord_label = chord_label
            _chord_change_times.appendleft(time.time())
        if scale_label:
            _last_scale_label = scale_label
    return _last_result


def get_scale_pcs():
    return set(_last_scale_pcs) if _last_scale_pcs else set()


def get_last_note():
    return _last_note


def get_recent_pcs():
    if not _recent_notes:
        return set()
    if RECENT_NOTE_SECONDS > 0:
        now = time.time()
        changed = False
        while _recent_notes and (now - _recent_notes[0][1]) > RECENT_NOTE_SECONDS:
            _recent_notes.popleft()
            changed = True
        if changed:
            global _last_pcs
            _last_pcs = None
        if not _recent_notes:
            return set()
    return {n % 12 for n, _ts in _recent_notes}


def get_key_histogram():
    counts = list(_key_counts)
    total = sum(counts)
    return counts, total


def get_chord_history():
    return list(CHORD_HISTORY)


def get_scale_history():
    labels = []
    for item in SCALE_HISTORY:
        if isinstance(item, dict):
            labels.append(item.get("label"))
    return labels


# Dissonance weights by interval class (0=unison … 6=tritone).
# Values drawn from psychoacoustic roughness literature; scaled so
# tritone and m2/M7 hit ~1.0 and P5/P4 are near 0.
_IC_DISSONANCE = [0.0, 1.0, 0.8, 0.3, 0.1, 0.2, 1.0]
_IC_NAMES      = ["",  "m2/M7", "M2/m7", "m3/M6", "M3/m6", "P4/P5", "tritone"]
_TENSION_LABELS = [
    (0.5,  "silent"),
    (2.5,  "consonant"),
    (4.5,  "mild"),
    (6.5,  "tense"),
    (8.5,  "dissonant"),
    (10.1, "harsh"),
]


def get_tension(active_pcs):
    """Return (score 0.0–10.0, label, worst_ic_name) for a set of pitch classes.

    active_pcs — iterable of MIDI pitch-class ints (0–11).
    Works on the *currently sounding* notes so the caller controls the window.
    """
    pcs = list(set(active_pcs))
    n = len(pcs)
    if n < 2:
        return 0.0, "silent", ""
    total = 0.0
    worst_ic = 0
    worst_w  = 0.0
    count = 0
    for i in range(n):
        for j in range(i + 1, n):
            ic = abs(pcs[i] - pcs[j]) % 12
            if ic > 6:
                ic = 12 - ic
            w = _IC_DISSONANCE[ic]
            total += w
            count += 1
            if w > worst_w:
                worst_w = w
                worst_ic = ic
    score = min(10.0, (total / count) * 10.0)
    label = _TENSION_LABELS[-1][1]
    for thresh, lbl in _TENSION_LABELS:
        if score < thresh:
            label = lbl
            break
    # Only name the interval when it's genuinely dissonant (m2/M7, M2/m7, tritone)
    worst_name = _IC_NAMES[worst_ic] if worst_w >= 0.5 else ""
    return score, label, worst_name


def get_scale_stats_list():
    stats = []
    for item in SCALE_HISTORY:
        if isinstance(item, dict):
            uniq_in = len(item["uniq_in"])
            uniq_total = len(item["uniq_total"])
            stats.append((f"{item['in']}/{item['total']}", f"{uniq_in}/{uniq_total}"))
    return stats


def get_chord_info():
    return _last_chord_info


def get_scale_info():
    return _last_scale_info


def get_harmonic_rhythm(bpm=120.0):
    """Return (changes_per_bar, label) based on recent chord change timestamps.

    Uses the last 4 chord-change intervals averaged together.
    Assumes 4/4 (4 beats per bar).
    Returns (None, '') if fewer than 2 chord changes recorded.
    """
    times = list(_chord_change_times)
    if len(times) < 2:
        return None, ""
    n = min(4, len(times) - 1)
    intervals = [times[i] - times[i + 1] for i in range(n)]
    avg_secs = sum(intervals) / len(intervals)
    if avg_secs <= 0:
        return None, ""
    secs_per_bar = (60.0 / max(bpm, 1.0)) * 4
    cpb = secs_per_bar / avg_secs
    if cpb < 0.15:
        label = "static"
    elif cpb < 0.6:
        label = "slow"
    elif cpb < 1.5:
        label = "moderate"
    elif cpb < 3.0:
        label = "fast"
    else:
        label = "very fast"
    return cpb, label


def get_motif_info(window=3):
    """Detect if the last `window` melodic intervals have appeared before.

    Transpositions are automatically matched because we track signed semitone
    intervals, not absolute pitches (e.g. up-M3 down-m2 matches anywhere).

    Returns (found, pattern_str, count) where count is occurrences in history.
    """
    hist = list(_interval_history)
    if len(hist) < window * 2:
        return False, "", 0
    current = tuple(hist[:window])
    count = 0
    for i in range(window, len(hist) - window + 1):
        if tuple(hist[i:i + window]) == current:
            count += 1
    if count == 0:
        return False, "", 0
    pat = " ".join(f"{'+' if x > 0 else ''}{x}" for x in current)
    return True, pat, count
