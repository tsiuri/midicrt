# pages/timesig_exp.py — Experimental time signature detection
BACKGROUND = True
PAGE_ID = 15
PAGE_NAME = "TimeSig Exp"

from midicrt import draw_line
from ui.model import PageLinesWidget
try:
    import plugins.ztimesig_exp as ztimesig_exp
except Exception:
    ztimesig_exp = None


def draw(state):
    cols = state["cols"]
    y0 = state.get("y_offset", 3)
    for idx, line in enumerate(_build_widget_lines(state)):
        draw_line(y0 + idx, line[:cols])


def _build_widget_lines(_state):
    lines = [f"--- {PAGE_NAME} ---"]
    if ztimesig_exp is None:
        return lines + ["(plugin unavailable)"]
    info = ztimesig_exp.get_timesig_exp()
    if not info:
        return lines + ["No lock yet"]
    labels = info.get("labels") or []
    conf = info.get("confidence", 0.0)
    events = info.get("events", 0)
    total = info.get("events_total", events)
    pending = info.get("pending")
    top = info.get("top")
    ts = labels[0] if len(labels) == 1 else " / ".join(labels)
    lines.append(f"Best: {ts}  conf:{conf:0.2f}  events:{events}/{total}")
    if pending:
        pend = " / ".join(pending) if isinstance(pending, (list, tuple)) else str(pending)
        lines.append(f"Pending change: {pend}")
    else:
        lines.append("")
    lines.extend(["", "Top candidates:"])
    if not top:
        lines.append("(no data)")
        return lines
    for i, (label, score) in enumerate(top[:3]):
        lines.append(f"{i+1}) {label:<5}  score:{score:0.3f}")
    return lines


def build_widget(state):
    return PageLinesWidget(page_id=PAGE_ID, page_name=PAGE_NAME, lines=_build_widget_lines(state))
