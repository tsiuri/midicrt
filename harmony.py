# harmony.py — chord/scale detection helpers

import csv
import os

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

DEGREE_BASE = {
    1: 0, 2: 2, 3: 4, 4: 5, 5: 7, 6: 9, 7: 11,
    8: 12, 9: 14, 10: 16, 11: 17, 12: 19, 13: 21,
}

BASE_DIR = os.path.dirname(__file__)
CHORDS_CSV = os.path.join(BASE_DIR, "config", "chords.csv")
SCALES_CSV = os.path.join(BASE_DIR, "config", "scales.csv")


def _note_name(pc):
    return NOTE_NAMES[pc % 12]


def _parse_intervals(intervals):
    pcs = set()
    for raw in intervals.split("-"):
        token = raw.strip()
        if not token or token.lower() == "x":
            continue
        acc = 0
        i = 0
        while i < len(token) and token[i] in ("b", "#", "x"):
            if token[i] == "b":
                acc -= 1
            elif token[i] == "#":
                acc += 1
            elif token[i] == "x":
                acc += 2
            i += 1
        deg = token[i:]
        if not deg.isdigit():
            continue
        base = DEGREE_BASE.get(int(deg))
        if base is None:
            continue
        pcs.add((base + acc) % 12)
    return pcs


def _load_db(path):
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("name") or "").strip()
                intervals = (row.get("intervals") or "").strip()
                aka = (row.get("aka") or "").strip()
                if not name or not intervals:
                    continue
                pcs = _parse_intervals(intervals)
                if not pcs:
                    continue
                rows.append({
                    "name": name,
                    "intervals": intervals,
                    "aka": aka,
                    "pcs": pcs,
                })
    except Exception:
        return []
    return rows


CHORDS = _load_db(CHORDS_CSV)
SCALES = _load_db(SCALES_CSV)


def _best_match(pcs, db, require_root_in_pcs=False, min_match=2, min_ratio=0.5):
    best = None
    for item in db:
        base = item["pcs"]
        for root in range(12):
            if require_root_in_pcs and root not in pcs:
                continue
            pattern = {(root + p) % 12 for p in base}
            match = len(pcs & pattern)
            if match < min_match:
                continue
            ratio = match / max(1, len(pcs))
            if ratio < min_ratio:
                continue
            extra = len(pcs - pattern)
            missing = len(pattern - pcs)
            score = (match, -extra, -missing, -len(pattern))
            if best is None or score > best["score"]:
                best = {
                    "root": root,
                    "name": item["name"],
                    "score": score,
                    "pattern_size": len(pattern),
                }
    return best


def _best_matches(pcs, db, require_root_in_pcs=False, min_match=2, min_ratio=0.5, max_ties=3):
    best_score = None
    hits = []
    for item in db:
        base = item["pcs"]
        for root in range(12):
            if require_root_in_pcs and root not in pcs:
                continue
            pattern = {(root + p) % 12 for p in base}
            match = len(pcs & pattern)
            if match < min_match:
                continue
            ratio = match / max(1, len(pcs))
            if ratio < min_ratio:
                continue
            extra = len(pcs - pattern)
            missing = len(pattern - pcs)
            score = (match, -extra, -missing, -len(pattern))
            if best_score is None or score > best_score:
                best_score = score
                hits = [{"root": root, "name": item["name"], "score": score}]
            elif score == best_score:
                hits.append({"root": root, "name": item["name"], "score": score})
    if not hits:
        return None
    if len(hits) > max_ties:
        return None
    return hits


def _make_info(hit, db, pcs=None):
    if not hit:
        return None
    base = None
    for item in db:
        if item["name"] == hit["name"]:
            base = item["pcs"]
            break
    if base is None:
        return None
    root = hit["root"]
    pattern = {(root + p) % 12 for p in base}
    label = f"{_note_name(root)} {hit['name']}"
    info = {
        "label": label,
        "root": root,
        "name": hit["name"],
        "pcs": pattern,
    }
    if pcs is not None:
        pcs_set = set(pcs)
        missing = sorted(pattern - pcs_set)
        match = len(pcs_set & pattern)
        ratio = (match / len(pattern)) if pattern else 0.0
        info["missing"] = missing
        info["match"] = match
        info["ratio"] = ratio
    return info


def _make_info_list(hits, db, pcs=None):
    if not hits:
        return None
    infos = []
    for hit in hits:
        info = _make_info(hit, db, pcs=pcs)
        if info:
            infos.append(info)
    return infos if infos else None


def detect_harmony_info(pcs, min_chord_notes=2, min_scale_notes=3,
                        chord_min_ratio=0.6, scale_min_ratio=0.7):
    pcs = set(pcs)
    chord = None
    scale = None

    if len(pcs) >= min_chord_notes:
        hits = _best_matches(
            pcs,
            CHORDS,
            require_root_in_pcs=True,
            min_match=min_chord_notes,
            min_ratio=chord_min_ratio,
            max_ties=3,
        )
        chord = _make_info_list(hits, CHORDS, pcs=pcs)

    if len(pcs) >= min_scale_notes:
        hits = _best_matches(
            pcs,
            SCALES,
            require_root_in_pcs=False,
            min_match=min_scale_notes,
            min_ratio=scale_min_ratio,
            max_ties=3,
        )
        scale = _make_info_list(hits, SCALES, pcs=pcs)

    return chord, scale


def detect_harmony(pcs, min_chord_notes=2, min_scale_notes=3,
                   chord_min_ratio=0.6, scale_min_ratio=0.7):
    chord, scale = detect_harmony_info(
        pcs,
        min_chord_notes=min_chord_notes,
        min_scale_notes=min_scale_notes,
        chord_min_ratio=chord_min_ratio,
        scale_min_ratio=scale_min_ratio,
    )
    def _label(info):
        if not info:
            return None
        if isinstance(info, list):
            return " / ".join(i["label"] for i in info)
        return info["label"]
    chord_label = _label(chord)
    scale_label = _label(scale)
    return chord_label, scale_label
