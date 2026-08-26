"""ui/ctrlgfx.py — shared pixel painters for controller pages.

  {"kind": "wavestrip", "row", "col", "cols", "rows", "labels", "selected", "focused", "title"}

Pages record geometry while building their text lines (row/col of each
gauge or plot); the painter overpaints those cell regions on the fb0
compositor with higher-resolution graphics. Text renderers keep the ASCII.

gfx item kinds:
  {"kind": "bar", "row", "col", "cols", "frac", "bipolar", "focused", "unknown"}
  {"kind": "env", "row", "col", "cols", "rows", "segments", "label", "focused"}
"""


def paint(canvas, items):
    cw, ch = canvas.cw, canvas.ch
    for it in items:
        x = canvas.col_px(it["col"])
        y = canvas.row_px(it["row"])
        if it["kind"] == "bar":
            w = it["cols"] * cw
            canvas.hbar(x, y, w, ch, it.get("frac", 0.0),
                        bipolar=it.get("bipolar", False),
                        focused=it.get("focused", False),
                        unknown=it.get("unknown", False))
        elif it["kind"] == "wavestrip":
            w = it["cols"] * cw
            h = it.get("rows", 1) * ch
            canvas.wave_strip(x, y, w, h, it["labels"], it["selected"],
                              focused=it.get("focused", False),
                              expand=it.get("expand", 1.6))
            if it.get("title"):
                from fb.canvas import GREEN_DIM
                canvas.text(x, y + h + 2, it["title"], fg=GREEN_DIM)
        elif it["kind"] == "env":
            w = it["cols"] * cw
            h = it["rows"] * ch
            canvas.envelope(x, y, w, h, it["segments"], label=it.get("label"),
                            focused=it.get("focused", False))


def adsr_segments(delay, attack, decay, sustain, release, amp=1.0,
                  sustain_hold=0.18):
    """Normalised (0..1) ADSR -> envelope segments for Canvas.envelope."""
    seg = lambda v: 0.06 + v * 0.3
    peak = amp
    sus = sustain * peak
    out = []
    if delay > 0:
        out.append((seg(delay) * 0.6, 0.0, 0.0))
    out += [(seg(attack), 0.0, peak), (seg(decay), peak, sus),
            (sustain_hold, sus, sus), (seg(release), sus, 0.0)]
    return out


def make_painter(items):
    snapshot = [dict(i) for i in items]
    return lambda canvas: paint(canvas, snapshot)


WAVE_FIELD_HINTS = ("waveform", "wave")


def is_wave_field(name, choices):
    """A choice field whose options are waveform names."""
    if not choices:
        return False
    n = name.lower()
    if not any(h in n for h in WAVE_FIELD_HINTS):
        return False
    return any(w in " ".join(choices).lower() for w in ("saw", "square", "pulse", "tri", "sine"))
