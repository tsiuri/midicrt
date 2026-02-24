# pages/chordkey.py — Chord confidence + Key estimator
BACKGROUND = True
PAGE_ID = 11
PAGE_NAME = "Chord+Key"

from midicrt import draw_line
from harmony import NOTE_NAMES, CHORDS
import plugins.zharmony as zharmony


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


def _stable_key_lines(stable):
    label = stable.get("label")
    conf = float(stable.get("confidence", 0.0))
    threshold = float(stable.get("threshold", 0.0))
    alts = stable.get("alternatives") or []
    ambiguous = bool(stable.get("ambiguous"))

    if not label:
        top = stable.get("top")
        if top:
            line1 = f"Key: ?  top:{top['label']} {int(round(top['ratio'] * 100)):d}%"
        else:
            line1 = "Key: ?"
    else:
        tag = "~" if ambiguous else "="
        line1 = f"Key{tag} {label}  {int(round(conf * 100)):d}% (thr {int(round(threshold * 100)):d}%)"

    if alts:
        alt_txt = " | ".join(f"{a['label']} {int(round(a['ratio'] * 100)):d}%" for a in alts[:2])
        line2 = f"alts: {alt_txt}"
    elif ambiguous:
        line2 = "alts: near-threshold / ambiguous"
    else:
        line2 = "alts: -"
    return line1, line2


def draw(state):
    cols = state["cols"]
    y0 = state.get("y_offset", 3)

    pcs = zharmony.get_recent_pcs()
    stable = zharmony.get_stable_key()

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
    draw_line(yk, "Stabilized key:".ljust(cols))
    k1, k2 = _stable_key_lines(stable)
    draw_line(yk + 1, k1[:cols])
    draw_line(yk + 2, k2[:cols])

    func = zharmony.get_last_function_label()
    if func:
        draw_line(yk + 3, f"Function: {func}"[:cols])
    else:
        draw_line(yk + 3, "Function: ?"[:cols])
