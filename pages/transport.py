# pages/transport.py — simple transport info
PAGE_ID = 3
PAGE_NAME = "Transport"

from midicrt import draw_line
try:
    import plugins.ztimesig as ztimesig
except Exception:
    ztimesig = None

def draw(state):
    draw_line(0, f"[{PAGE_ID}] {PAGE_NAME}")
    draw_line(2, f"Running: {state['running']}")
    draw_line(3, f"Bar Counter: {state['bar']}")
    draw_line(4, f"BPM: {state['bpm']:5.1f}")
    draw_line(5, f"Ticks: {state['tick']}")
    if ztimesig is None:
        draw_line(6, "Time Signature: (unavailable)")
        return
    info = ztimesig.get_timesig()
    if not info:
        draw_line(6, "Time Signature: (no lock)")
        return
    labels = info.get("labels") or []
    conf = info.get("confidence", 0.0)
    events = info.get("events", 0)
    win = info.get("events_window", events)
    total = info.get("events_total", events)
    pending = info.get("pending")
    if len(labels) == 1:
        ts = labels[0]
    else:
        ts = " / ".join(labels)
    if pending:
        pend = " / ".join(pending) if isinstance(pending, (list, tuple)) else str(pending)
        draw_line(6, f"Time Signature: {ts}  conf:{conf:0.2f}  events:{win}/{total}  -> {pend}")
    else:
        draw_line(6, f"Time Signature: {ts}  conf:{conf:0.2f}  events:{win}/{total}")
