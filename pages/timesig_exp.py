# pages/timesig_exp.py — Experimental time signature detection
BACKGROUND = True
PAGE_ID = 15
PAGE_NAME = "TimeSig Exp"

from midicrt import draw_line
try:
    import plugins.ztimesig_exp as ztimesig_exp
except Exception:
    ztimesig_exp = None


def draw(state):
    cols = state["cols"]
    y0 = state.get("y_offset", 3)

    draw_line(y0, f"--- {PAGE_NAME} ---".ljust(cols))
    if ztimesig_exp is None:
        draw_line(y0 + 1, "(plugin unavailable)".ljust(cols))
        return

    info = ztimesig_exp.get_timesig_exp()
    if not info:
        draw_line(y0 + 1, "No lock yet".ljust(cols))
        return

    labels = info.get("labels") or []
    conf = info.get("confidence", 0.0)
    events = info.get("events", 0)
    total = info.get("events_total", events)
    pending = info.get("pending")
    top = info.get("top")

    if len(labels) == 1:
        ts = labels[0]
    else:
        ts = " / ".join(labels)

    line = f"Best: {ts}  conf:{conf:0.2f}  events:{events}/{total}"
    draw_line(y0 + 1, line[:cols])

    if pending:
        pend = " / ".join(pending) if isinstance(pending, (list, tuple)) else str(pending)
        draw_line(y0 + 2, f"Pending change: {pend}"[:cols])
    else:
        draw_line(y0 + 2, "".ljust(cols))

    draw_line(y0 + 4, "Top candidates:".ljust(cols))
    if not top:
        draw_line(y0 + 5, "(no data)".ljust(cols))
        return
    for i, (label, score) in enumerate(top[:3]):
        draw_line(y0 + 5 + i, f"{i+1}) {label:<5}  score:{score:0.3f}"[:cols])
