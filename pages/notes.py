# pages/notes.py — main notes/velocity view
PAGE_ID = 1
PAGE_NAME = "Notes"

import sys
import time
import midicrt
import harmony as _harmony
from midicrt import draw_line, INSTRUMENT_NAMES
from plugins import polydisplay
from plugins import zharmony
from engine.page_contracts import build_legacy_page_view_contract
from ui.view_contracts import lines_from_page_view_contract
from ui.model import Line, NotesWidget

term = midicrt.term

_tension_held = (0.0, "silent", "")
_tension_held_ts = 0.0
_TENSION_HOLD_SECS = 1.5

def _render_slots(title, values, cols, y, reverse_first=False):
    slot_w = max(10, (cols - len(title) - 2) // 4)
    labels = ["Last", "2nd", "3rd", "4th"]
    header = title + " " + "".join(l.ljust(slot_w) for l in labels)
    draw_line(y, header[:cols])

    vals = []
    for i in range(4):
        text = values[i] if i < len(values) and values[i] else "--"
        vals.append(text[:slot_w].ljust(slot_w))
    line = title + " " + "".join(vals)
    draw_line(y + 1, line[:cols])

    if reverse_first:
        text = (values[0] if values else "--") or "--"
        text = text[:slot_w].ljust(slot_w)
        x = len(title) + 1
        if x < cols:
            sys.stdout.write(term.move_yx(y + 1, x) + term.reverse(text) + term.normal)

def _render_row(title, values, cols, y, reverse_second=False):
    slot_w = max(10, (cols - len(title) - 2) // 4)
    vals = []
    slot_meta = []
    for i in range(4):
        item = values[i] if i < len(values) and values[i] else "--"
        if isinstance(item, tuple) and len(item) == 2:
            normal, rev = item
            normal = normal or "--"
            rev = rev or "--"
            text = f"{normal} {rev}"
            slot_meta.append((normal, rev))
        else:
            text = str(item)
            slot_meta.append((text, None))
        vals.append(text[:slot_w].ljust(slot_w))
    line = title + " " + "".join(vals)
    draw_line(y, line[:cols])

    if reverse_second:
        x = len(title) + 1
        for i in range(4):
            normal, rev = slot_meta[i]
            if rev:
                # reverse only the unique fraction (rev), keep normal fraction plain
                offset = len(str(normal)) + 1
                if offset < slot_w:
                    rev_text = str(rev)[: max(0, slot_w - offset)]
                    sys.stdout.write(term.move_yx(y, x + offset) + term.reverse(rev_text) + term.normal)
            x += slot_w

def draw(state):
    y0 = state.get("y_offset", 3)
    rows = state.get("rows", 0)
    cols = state.get("cols", 0)
    for ch, name in enumerate(INSTRUMENT_NAMES, start=1):
        y = y0 + (ch - 1)
        notes = polydisplay.get_notes(ch)
        line = f"{ch:02d}  {name:<11}  {notes}"
        draw_line(y, line)

    info_y = y0 + len(INSTRUMENT_NAMES)
    if rows and info_y + 4 <= rows - 1:
        chord, scale = zharmony.get_harmony()
        chord_info = zharmony.get_chord_info()
        scale_info = zharmony.get_scale_info()
        active_pcs = set()
        for notes in polydisplay.active_notes.values():
            for (note, _vel) in notes:
                active_pcs.add(note % 12)

        chord_hist = zharmony.get_chord_history()
        scale_hist = zharmony.get_scale_history()
        scale_stats = zharmony.get_scale_stats_list()

        chord_rev = bool(chord and active_pcs)
        last_note = zharmony.get_last_note()
        scale_pcs = zharmony.get_scale_pcs()
        scale_rev = bool(scale and last_note is not None and (last_note % 12) in scale_pcs)

        _render_slots("Chord:", chord_hist, cols, info_y, reverse_first=chord_rev)
        _render_slots("Scale:", scale_hist, cols, info_y + 2, reverse_first=scale_rev)
        # render Inside: with unique fraction in reverse text
        # values are (total_frac, unique_frac)
        _render_row("Inside:", scale_stats, cols, info_y + 4, reverse_second=True)

        # confidence + missing tones
        if info_y + 8 <= rows - 1:
            def _fmt_missing(pcs):
                if not pcs:
                    return "-"
                return " ".join(_harmony.NOTE_NAMES[p % 12] for p in pcs)

            def _first_info(info):
                if isinstance(info, list) and info:
                    return info[0]
                if isinstance(info, dict):
                    return info
                return None

            cinfo = _first_info(chord_info)
            sinfo = _first_info(scale_info)
            if cinfo:
                miss = cinfo.get("missing", [])
                conf = cinfo.get("ratio", 0.0)
                miss_txt = _fmt_missing(miss)
                draw_line(info_y + 6, f"Chord conf: {conf:0.2f}  missing: {miss_txt or '-'}"[:cols])
            else:
                draw_line(info_y + 6, "Chord conf: --  missing: -"[:cols])
            if sinfo:
                miss = sinfo.get("missing", [])
                conf = sinfo.get("ratio", 0.0)
                miss_txt = _fmt_missing(miss)
                draw_line(info_y + 7, f"Scale conf: {conf:0.2f}  missing: {miss_txt or '-'}"[:cols])
            else:
                draw_line(info_y + 7, "Scale conf: --  missing: -"[:cols])

        if info_y + 8 <= rows - 1:
            stable = zharmony.get_stable_key()
            key_label = stable.get("label") or "?"
            alts = stable.get("alternatives") or []
            amb = stable.get("ambiguous")
            if key_label == "?" and stable.get("top"):
                key_label = f"?→{stable['top']['label']}"
            if alts and amb:
                alt_txt = ", ".join(a["label"] for a in alts[:2])
                key_line = f"Key: {key_label}  (alts: {alt_txt})"
            elif amb:
                key_line = f"Key: {key_label}  (ambiguous)"
            else:
                key_line = f"Key: {key_label}"
            func = zharmony.get_last_function_label() or "?"
            draw_line(info_y + 8, f"{key_line}  Fn: {func}"[:cols])

        if info_y + 9 <= rows - 1:
            global _tension_held, _tension_held_ts
            if len(active_pcs) >= 2:
                _tension_held = zharmony.get_tension(active_pcs)
                _tension_held_ts = time.time()
                t_score, t_label, t_worst = _tension_held
            elif time.time() - _tension_held_ts < _TENSION_HOLD_SECS:
                t_score, t_label, t_worst = _tension_held
            else:
                t_score, t_label, t_worst = 0.0, "silent", ""
            bar_max = 20
            filled = round(t_score / 10.0 * bar_max)
            bar = "█" * filled + "░" * (bar_max - filled)
            worst_str = f"  [{t_worst}]" if t_worst else ""
            t_line = f"Tension: {bar}  {t_score:.1f}  {t_label}{worst_str}"
            draw_line(info_y + 9, t_line[:cols])

        if info_y + 10 <= rows - 1:
            bpm = getattr(midicrt, "bpm", 120.0) or 120.0
            cpb, hr_label = zharmony.get_harmonic_rhythm(bpm)
            if cpb is not None:
                hr_line = f"Harm.rhy: {cpb:.1f} ch/bar  {hr_label}"
            else:
                hr_line = "Harm.rhy: --"
            draw_line(info_y + 10, hr_line[:cols])

        if info_y + 11 <= rows - 1:
            found, pat, count = zharmony.get_motif_info()
            if found:
                motif_line = f"Motif:  {pat}  [x{count}]"
            else:
                motif_line = "Motif:  --"
            draw_line(info_y + 11, motif_line[:cols])


def build_widget(state):
    payload = build_legacy_page_view_contract(draw, state, draw_line).as_dict()
    lines = lines_from_page_view_contract(payload)
    return NotesWidget(lines=[Line.plain(t) for t in lines])
