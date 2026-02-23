# pages/stuckheat.py — Stuck note heatmap (prototype)
BACKGROUND = True
PAGE_ID = 12
PAGE_NAME = "Stuck Heatmap"

from midicrt import draw_line
import plugins.zstucknotes as zstucknotes

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _note_name(pc):
    return NOTE_NAMES[pc % 12]


def _fmt_note(note):
    name = NOTE_NAMES[note % 12]
    octave = (note // 12) - 1 + 2
    return f"{name}{octave}({note:03d})"


def draw(state):
    cols = state["cols"]
    y0 = state.get("y_offset", 3)

    stats = zstucknotes.get_stuck_stats()
    pc_counts = stats.get("pc_counts", {})
    note_counts = stats.get("note_counts", {})

    draw_line(y0, f"--- {PAGE_NAME} ---".ljust(cols))
    draw_line(y0 + 1, "Counts by pitch class (warn/crit events):".ljust(cols))

    row1 = []
    row2 = []
    for i, name in enumerate(NOTE_NAMES):
        count = pc_counts.get(i, 0)
        token = f"{name}:{count}"
        if i < 6:
            row1.append(token)
        else:
            row2.append(token)

    draw_line(y0 + 2, ("  ".join(row1))[:cols])
    draw_line(y0 + 3, ("  ".join(row2))[:cols])

    draw_line(y0 + 5, "Top stuck notes:".ljust(cols))
    if not note_counts:
        draw_line(y0 + 6, "(none yet)".ljust(cols))
        draw_line(y0 + 7, "".ljust(cols))
        draw_line(y0 + 8, "".ljust(cols))
    else:
        top = sorted(note_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
        parts = [f"{_fmt_note(n)}:{c}" for n, c in top]
        line = " | ".join(parts)
        draw_line(y0 + 6, line[:cols])
        draw_line(y0 + 7, "".ljust(cols))
        draw_line(y0 + 8, "".ljust(cols))
