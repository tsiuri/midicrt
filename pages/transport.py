# pages/transport.py — simple transport info
PAGE_ID = 3
PAGE_NAME = "Transport"

from ui.model import Column, Line, TextBlock

try:
    import plugins.ztimesig as ztimesig
except Exception:
    ztimesig = None


def _timesig_line():
    if ztimesig is None:
        return "Time Signature: (unavailable)"
    info = ztimesig.get_timesig()
    if not info:
        return "Time Signature: (no lock)"
    labels = info.get("labels") or []
    conf = info.get("confidence", 0.0)
    events = info.get("events", 0)
    win = info.get("events_window", events)
    total = info.get("events_total", events)
    pending = info.get("pending")
    ts = labels[0] if len(labels) == 1 else " / ".join(labels)
    if pending:
        pend = " / ".join(pending) if isinstance(pending, (list, tuple)) else str(pending)
        return f"Time Signature: {ts}  conf:{conf:0.2f}  events:{win}/{total}  -> {pend}"
    return f"Time Signature: {ts}  conf:{conf:0.2f}  events:{win}/{total}"


def build_widget(state):
    return Column(
        children=[
            TextBlock(lines=[Line.plain(f"[{PAGE_ID}] {PAGE_NAME}")]),
            TextBlock(lines=[Line.plain("")]),
            TextBlock(lines=[Line.plain(f"Running: {state['running']}")]),
            TextBlock(lines=[Line.plain(f"Bar Counter: {state['bar']}")]),
            TextBlock(lines=[Line.plain(f"BPM: {state['bpm']:5.1f}")]),
            TextBlock(lines=[Line.plain(f"Ticks: {state['tick']}")]),
            TextBlock(lines=[Line.plain(_timesig_line())]),
        ]
    )
