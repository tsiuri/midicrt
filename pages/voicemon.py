# pages/voicemon.py — Voice/Polyphony monitor (prototype)
BACKGROUND = True
PAGE_ID = 13
PAGE_NAME = "Voice Monitor"

import time
import midicrt
from midicrt import draw_line
import plugins.zvoicemonitor as zvoice
from ui.adapters import build_widget_from_legacy_draw

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _fmt_note(note):
    if note is None:
        return "--"
    name = NOTE_NAMES[note % 12]
    octave = (note // 12) - 1 + 2
    return f"{name}{octave}({note:03d})"


def _fmt_chan(ch, active, limit, width, names, peak=0, warn=False):
    try:
        name = names[ch - 1]
    except Exception:
        name = f"Ch{ch}"
    name = str(name)
    name = name[:10]
    token = f"{ch:02d} {name:<10} {active}/{limit} pk{peak}"
    if warn:
        token += " !"
    return token.ljust(width)


def draw(state):
    cols = state["cols"]
    y0 = state.get("y_offset", 3)

    stats = zvoice.get_voice_stats()
    total = stats.get("total", 0)
    peak = stats.get("peak_total", 0)
    per_ch = stats.get("per_ch", {})
    peak_ch = stats.get("peak_ch", {})
    per_ch_limits = stats.get("per_ch_limits", [zvoice.POLY_LIMIT_CH] * 16)
    over_warn = stats.get("over_warned", {})
    events = stats.get("events", [])

    draw_line(y0, f"--- {PAGE_NAME} ---".ljust(cols))
    line = f"Active total: {total}  Peak: {peak}  Limit global {zvoice.POLY_LIMIT_GLOBAL}"
    draw_line(y0 + 1, line[:cols])

    names = getattr(midicrt, "INSTRUMENT_NAMES", [f"Ch{c}" for c in range(1, 17)])
    col_width = max(24, cols // 2)
    for i in range(8):
        ch_left = i + 1
        ch_right = i + 9
        left = _fmt_chan(
            ch_left,
            per_ch.get(ch_left, 0),
            per_ch_limits[ch_left - 1],
            col_width,
            names,
            peak=peak_ch.get(ch_left, 0),
            warn=over_warn.get(ch_left, False),
        )
        if ch_right <= 16:
            right = _fmt_chan(
                ch_right,
                per_ch.get(ch_right, 0),
                per_ch_limits[ch_right - 1],
                col_width,
                names,
                peak=peak_ch.get(ch_right, 0),
                warn=over_warn.get(ch_right, False),
            )
        else:
            right = ""
        draw_line(y0 + 2 + i, (left + right)[:cols])

    draw_line(y0 + 11, "Over-limit events:".ljust(cols))
    if not events:
        draw_line(y0 + 12, "(none yet)".ljust(cols))
        draw_line(y0 + 13, "".ljust(cols))
        draw_line(y0 + 14, "".ljust(cols))
    else:
        now = time.time()
        for i, ev in enumerate(events[:3]):
            if len(ev) >= 9:
                ts, ch, note, total_now, ch_now, ch_lim, hit_global, hit_ch, tag = ev
            else:
                ts, ch, note, total_now, ch_now, ch_lim, hit_global, hit_ch = ev
                tag = "instant"
            age = now - ts
            try:
                name = names[ch - 1]
            except Exception:
                name = f"Ch{ch}"
            flags = []
            if hit_global:
                flags.append("global")
            if hit_ch:
                flags.append("ch")
            if tag == "sustain":
                flags.append("sustain")
            tag = ",".join(flags) if flags else "-"
            line = f"{i+1}) {age:4.1f}s  CH{ch:02d} {name} {_fmt_note(note)}  total:{total_now} ch:{ch_now}/{ch_lim}  {tag}"
            draw_line(y0 + 12 + i, line[:cols])
        for j in range(len(events), 3):
            draw_line(y0 + 12 + j, "".ljust(cols))


def build_widget(state):
    return build_widget_from_legacy_draw(draw, state, draw_line)
