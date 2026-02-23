# pages/chordkey.py — Chord confidence + Key estimator
BACKGROUND = True
PAGE_ID = 11
PAGE_NAME = "Chord+Key"

from midicrt import draw_line
from harmony import NOTE_NAMES, CHORDS
import plugins.zharmony as zharmony

MAJOR_SCALE = [0, 2, 4, 5, 7, 9, 11]
MINOR_SCALE = [0, 2, 3, 5, 7, 8, 10]


def _note_name(pc):
    return NOTE_NAMES[pc % 12]


def _format_pcs(pcs):
    if not pcs:
        return "(none)"
    return " ".join(_note_name(pc) for pc in sorted(pcs))


def _chord_candidates(pcs):
    pcs_set = set(pcs)
    if not pcs_set:
        return []
    results = []
    seen = set()
    for item in CHORDS:
        base = item.get("pcs", set())
        for root in range(12):
            pattern = {(root + p) % 12 for p in base}
            match = len(pcs_set & pattern)
            if match < 2:
                continue
            missing = sorted(pattern - pcs_set)
            extra = len(pcs_set - pattern)
            ratio = match / max(1, len(pattern))
            score = (ratio, match, -extra, -len(missing))
            label = f"{_note_name(root)} {item['name']}"
            if label in seen:
                continue
            seen.add(label)
            results.append({
                "label": label,
                "ratio": ratio,
                "match": match,
                "missing": missing,
                "extra": extra,
                "score": score,
            })
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:3]


def _key_candidates(counts, total):
    if total <= 0:
        return []
    results = []
    for root in range(12):
        for mode, intervals in (("maj", MAJOR_SCALE), ("min", MINOR_SCALE)):
            scale = {(root + i) % 12 for i in intervals}
            inside = sum(counts[pc] for pc in scale)
            outside = total - inside
            ratio = inside / total
            score = ratio - (outside / total) * 0.5
            results.append({
                "label": f"{_note_name(root)} {mode}",
                "ratio": ratio,
                "inside": inside,
                "outside": outside,
                "score": score,
            })
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:3]


def draw(state):
    cols = state["cols"]
    y0 = state.get("y_offset", 3)

    pcs = zharmony.get_recent_pcs()
    counts, total = zharmony.get_key_histogram()

    draw_line(y0, f"--- {PAGE_NAME} ---".ljust(cols))
    draw_line(y0 + 1, f"Recent PCs: {_format_pcs(pcs)}"[:cols])

    draw_line(y0 + 2, "Chord candidates:".ljust(cols))
    chords = _chord_candidates(pcs)
    if not chords:
        draw_line(y0 + 3, "(no chord match yet)".ljust(cols))
        draw_line(y0 + 4, "".ljust(cols))
        draw_line(y0 + 5, "".ljust(cols))
    else:
        for i, cand in enumerate(chords):
            miss = " ".join(_note_name(pc) for pc in cand["missing"]) or "-"
            pct = int(round(cand["ratio"] * 100))
            line = f"{i+1}) {cand['label']}  {pct:3d}%  missing:{miss}"
            draw_line(y0 + 3 + i, line[:cols])
        for j in range(len(chords), 3):
            draw_line(y0 + 3 + j, "".ljust(cols))

    yk = y0 + 7
    draw_line(yk, f"Key estimate (last {total} notes):".ljust(cols))
    keys = _key_candidates(counts, total)
    if not keys:
        draw_line(yk + 1, "(no key yet)".ljust(cols))
        draw_line(yk + 2, "".ljust(cols))
        draw_line(yk + 3, "".ljust(cols))
    else:
        for i, cand in enumerate(keys):
            pct = int(round(cand["ratio"] * 100))
            line = f"{i+1}) {cand['label']}  {pct:3d}%  in:{cand['inside']}/{total}"
            draw_line(yk + 1 + i, line[:cols])
        for j in range(len(keys), 3):
            draw_line(yk + 1 + j, "".ljust(cols))
