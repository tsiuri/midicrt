# pages/chordkey.py — Chord confidence + Key estimator
BACKGROUND = True
PAGE_ID = 11
PAGE_NAME = "Chord+Key"

from midicrt import draw_line
from harmony import NOTE_NAMES, CHORDS
import plugins.zharmony as zharmony
from ui.model import PageLinesWidget


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
    lines = _build_widget_lines(state)
    cols = state["cols"]
    y0 = state.get("y_offset", 3)
    for idx, line in enumerate(lines):
        draw_line(y0 + idx, line[:cols])


def _build_widget_lines(_state):
    pcs = zharmony.get_recent_pcs()
    stable = zharmony.get_stable_key()
    lines = [f"--- {PAGE_NAME} ---", f"Recent PCs: {_format_pcs(pcs)}", "Chord candidates:"]

    chords = _chord_candidates(pcs)
    if not chords:
        lines.extend(["(no chord match yet)", "", ""])
    else:
        for i, cand in enumerate(chords):
            miss = " ".join(_note_name(pc) for pc in cand["missing"]) or "-"
            pct = int(round(cand["ratio"] * 100))
            lines.append(f"{i+1}) {cand['label']}  {pct:3d}%  missing:{miss}")
        for _ in range(len(chords), 3):
            lines.append("")

    lines.append("")
    lines.append("Stabilized key:")
    k1, k2 = _stable_key_lines(stable)
    lines.extend([k1, k2])
    func = zharmony.get_last_function_label()
    lines.append(f"Function: {func}" if func else "Function: ?")
    return lines


def build_widget(state):
    return PageLinesWidget(page_id=PAGE_ID, page_name=PAGE_NAME, lines=_build_widget_lines(state))
