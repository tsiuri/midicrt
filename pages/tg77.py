# pages/tg77.py — Yamaha TG77 controller page
#
# Device definition + byte-exact builders: devices/tg77.py (ported from the
# TG77ControllerVST3 JUCE VST — the protocol authority).  Same contract as
# the LXP-1 / Matrix-1000 pages:
#   [ / ]     previous / next group (OP1-6, Global, Filter, Setup, AWM)
#   arrows    cursor / nudge (Shift = coarse); Enter typed entry
#   s         cycle element slot 1-4      f  toggle filter bank 1/2
#   d         typed device number 0-15    x/v  panel Cancel / Exit
#   X         cancel burst (3x Cancel + 3x Exit, 35ms spacing)
#   , / .     MIDI channel   L  knob learn (absolute full-range)

BACKGROUND = False
PAGE_ID = 20
PAGE_NAME = "TG77 Ctrl"
DEVICE_ID = "tg77"

import threading
import time

import mido

from midicrt import draw_line
from configutil import load_section, save_section
from ui.model import PageLinesWidget, CanvasWidget
from ui import ctrlgfx
from devices import tg77 as DEV

_cfg = {}
try:
    _cfg = load_section("tg77") or {}
except Exception:
    _cfg = {}

channel = int(_cfg.get("channel", DEV.DEFAULT_CHANNEL))
device_number = int(_cfg.get("device_number", DEV.DEFAULT_DEVICE_NUMBER))
element_slot = int(_cfg.get("element_slot", 0))     # 0-3 (shown 1-4)
filter_select = int(_cfg.get("filter_select", 0))   # 0-1 (shown 1-2)
values = _cfg.get("values", {}) if isinstance(_cfg.get("values"), dict) else {}
output_hints = _cfg.get("output_hints", ["UX16", "USB MIDI", "MIDI 1"])

note_target_channel = channel

_OP_GROUPS = [f"OP{i+1}" for i in range(6)]
GROUPS = _OP_GROUPS + ["Global", "Filter", "Setup", "AWM"]
group_idx = 0
cursor = 0
entry_mode = None
entry_buf = ""
_entry_prev_lock = None
status_msg = ""
status_time = 0.0
last_tx = ""
out_port = None
out_err = ""
_save_pending = 0.0
_gfx = []

_SIGN_CHOICES = ["+", "-"]


def _specs_for_group(g):
    """[(spec, family)] — family drives shadow-key scoping."""
    if g in _OP_GROUPS:
        return [(s, "op") for s in DEV.OPERATOR_SPECS]
    if g == "Global":
        return [(s, "g") for s in DEV.GLOBAL_SPECS]
    if g == "Filter":
        return ([(s, "flt") for s in DEV.FILTER_BANK_SPECS]
                + [(s, "g") for s in DEV.FILTER_COMMON_SPECS])
    if g == "Setup":
        return [(s, "g") for s in DEV.SETUP_SPECS]
    return [(s, "g") for s in DEV.AWM_SPECS]


def _shadow_key(family, sid):
    if family == "op":
        return f"o{group_idx}.{sid}"
    if family == "flt":
        return f"f{filter_select}.{sid}"
    return f"g.{sid}"


_ALL_SPECS = {}
for _s in (list(DEV.OPERATOR_SPECS) + list(DEV.GLOBAL_SPECS)
           + list(DEV.FILTER_BANK_SPECS) + list(DEV.FILTER_COMMON_SPECS)
           + list(DEV.SETUP_SPECS) + list(DEV.AWM_SPECS)):
    _ALL_SPECS[_s[0]] = _s


def _family_of(sid):
    if any(s[0] == sid for s in DEV.OPERATOR_SPECS):
        return "op"
    if any(s[0] == sid for s in DEV.FILTER_BANK_SPECS):
        return "flt"
    return "g"


def _get_value(family, sid):
    spec = _ALL_SPECS[sid]
    return int(values.get(_shadow_key(family, sid), spec[4]))


def _fields():
    g = GROUPS[group_idx % len(GROUPS)]
    out = []
    for spec, family in _specs_for_group(g):
        sid, label, mn, mx, dflt, choices = spec
        if choices is None and sid.lower().endswith("sign"):
            choices = _SIGN_CHOICES
        out.append({"sid": sid, "family": family, "name": label,
                    "min": mn, "max": mx, "default": dflt, "choices": choices})
    return out


def _mark_save():
    global _save_pending
    _save_pending = time.time() + 2.0


def _flush_save():
    global _save_pending
    if _save_pending and time.time() >= _save_pending:
        _save_pending = 0.0
        try:
            save_section("tg77", {
                "channel": channel, "device_number": device_number,
                "element_slot": element_slot, "filter_select": filter_select,
                "values": values, "output_hints": output_hints,
            })
        except Exception:
            pass


def _status(text):
    global status_msg, status_time
    status_msg = text
    status_time = time.time()


def _ensure_out():
    global out_port, out_err
    if out_port is not None:
        return True
    try:
        names = list(mido.get_output_names())
    except Exception as exc:
        out_err = f"midi backend: {exc}"
        return False
    for hint in output_hints:
        hl = str(hint).lower()
        for name in names:
            if hl in name.lower():
                try:
                    out_port = mido.open_output(name)
                    out_err = ""
                    return True
                except Exception as exc:
                    out_err = f"open {name}: {exc}"
    if not out_err:
        out_err = f"no output matching {output_hints}"
    return False


def _send_messages(msgs, desc):
    global last_tx
    if not _ensure_out():
        _status(f"TX FAILED: {out_err}")
        return False
    try:
        for m in msgs:
            out_port.send(mido.Message("sysex", data=list(m)))
        last_tx = desc + "  " + " ".join(f"{b:02X}" for b in msgs[-1])
        return True
    except Exception as exc:
        _status(f"TX FAILED: {exc}")
        return False


def _send_field(f):
    g = GROUPS[group_idx % len(GROUPS)]
    op = group_idx if g in _OP_GROUPS else None
    try:
        msgs = DEV.build_messages(
            f["sid"],
            lambda sid: _get_value(_family_of(sid), sid),
            op=op, element_slot=element_slot,
            filter_select=filter_select, device_number=device_number)
    except Exception as exc:
        _status(f"build failed: {exc}")
        return False
    return _send_messages(msgs, f["sid"])


def _set_value(f, v, send=True):
    v = max(f["min"], min(f["max"], int(v)))
    values[_shadow_key(f["family"], f["sid"])] = v
    _mark_save()
    if send:
        _send_field(f)
    return v


def _display(f, v):
    if f["choices"]:
        i = v - f["min"]
        if 0 <= i < len(f["choices"]):
            return f["choices"][i]
    return str(v)


def _nudge(delta):
    flds = _fields()
    f = flds[min(cursor, len(flds) - 1)]
    v = _set_value(f, _get_value(f["family"], f["sid"]) + delta)
    _status(f"{f['name']} = {_display(f, v)}")


def on_knob_value(value):
    flds = _fields()
    f = flds[min(cursor, len(flds) - 1)]
    frac = max(0, min(127, int(value))) / 127.0
    v = round(f["min"] + frac * (f["max"] - f["min"]))
    if v == _get_value(f["family"], f["sid"]):
        return
    v = _set_value(f, v)
    _status(f"{f['name']} = {_display(f, v)}")


def on_knob_delta(delta):
    _nudge(int(delta))


def _entry_begin(mode):
    global entry_mode, entry_buf, _entry_prev_lock
    import midicrt as _m
    entry_mode = mode
    entry_buf = ""
    _entry_prev_lock = getattr(_m, "_page_locked", False)
    _m._page_locked = True


def _entry_end():
    global entry_mode, entry_buf, _entry_prev_lock
    import midicrt as _m
    entry_mode = None
    entry_buf = ""
    if _entry_prev_lock is not None:
        _m._page_locked = _entry_prev_lock
    _entry_prev_lock = None


def _entry_commit():
    global device_number
    mode, buf = entry_mode, entry_buf.strip()
    _entry_end()
    if not buf:
        return
    try:
        num = int(float(buf))
    except ValueError:
        _status(f"bad number: {buf!r}")
        return
    if mode == "value":
        flds = _fields()
        f = flds[min(cursor, len(flds) - 1)]
        v = _set_value(f, num)
        _status(f"{f['name']} = {_display(f, v)}")
    elif mode == "devnum":
        device_number = max(0, min(15, num))
        _mark_save()
        _status(f"device number {device_number}")


def _cancel_burst_thread():
    msgs = DEV.panel_cancel_burst(device_number)
    def run():
        for m in msgs:
            _send_messages([m], "panel burst")
            time.sleep(DEV.CANCEL_BURST_DELAY_SECONDS)
    threading.Thread(target=run, daemon=True).start()
    _status("cancel burst sent (3x Cancel + 3x Exit)")


def mapping_key_for_cursor():
    flds = _fields()
    f = flds[min(cursor, len(flds) - 1)]
    g = GROUPS[group_idx % len(GROUPS)]
    if f["family"] == "op":
        scope = f"o{group_idx}"
    elif f["family"] == "flt":
        scope = f"f{filter_select}"
    else:
        scope = "g"
    return f"{scope}.{f['sid']}", f"{g} {f['name']}"


def on_mapped_set(key, v):
    values[key] = int(v)
    _mark_save()


# --- external MIDI mapping (plugins/midimap.py) ------------------------------

def _midimap():
    import midicrt as _m
    return next((p for p in _m.PLUGINS if getattr(p, "__name__", "").endswith("midimap")), None)


def _map_learn():
    mm = _midimap()
    if mm is None:
        _status("midimap plugin not loaded")
        return
    key, label = mapping_key_for_cursor()
    mm.arm_learn(DEVICE_ID, key, label)
    _status(f"MAP LEARN armed for {label}: move a knob on the Cirklon/controller")


def _map_unbind():
    mm = _midimap()
    if mm is None:
        return
    key, label = mapping_key_for_cursor()
    _status(f"unmapped {label}" if mm.unbind(DEVICE_ID, key) else f"{label} had no mapping")


def _map_text():
    mm = _midimap()
    if mm is None:
        return ""
    ls = mm.learn_status()
    if ls:
        return ls
    key, label = mapping_key_for_cursor()
    d = mm.describe(DEVICE_ID, key)
    return f"map: {d}" if d else "map: (none — M to learn)"


def _arm_learn():
    import midicrt as _m
    knob = next((p for p in _m.PLUGINS if hasattr(p, "arm_learn")), None)
    if knob is None:
        _status("knobctl plugin not loaded")
        return
    knob.arm_learn()
    _status("knob learn armed: turn the knob you want bound")


def _knob_status():
    import midicrt as _m
    knob = next((p for p in _m.PLUGINS if hasattr(p, "knob_status")), None)
    return knob.knob_status() if knob else "knobctl off"


def keypress(key):
    global cursor, group_idx, channel, element_slot, filter_select
    global entry_buf, note_target_channel
    kname = key.name if getattr(key, "is_sequence", False) else ""
    s = "" if kname else str(key)

    if entry_mode is not None:
        if kname == "KEY_ENTER" or s in ("\r", "\n"):
            _entry_commit()
            return True
        if kname == "KEY_ESCAPE":
            _entry_end()
            _status("entry cancelled")
            return True
        if kname in ("KEY_BACKSPACE", "KEY_DELETE") or s == "\x7f":
            entry_buf = entry_buf[:-1]
            return True
        if s and (s.isdigit() or (s == "-" and not entry_buf)):
            entry_buf += s
            return True
        return True

    flds = _fields()
    if s == "[":
        group_idx = (group_idx - 1) % len(GROUPS)
        cursor = 0
        return True
    if s == "]":
        group_idx = (group_idx + 1) % len(GROUPS)
        cursor = 0
        return True
    if kname == "KEY_UP":
        cursor = (cursor - 1) % len(flds)
        return True
    if kname == "KEY_DOWN":
        cursor = (cursor + 1) % len(flds)
        return True
    if kname == "KEY_LEFT":
        _nudge(-1)
        return True
    if kname == "KEY_RIGHT":
        _nudge(1)
        return True
    if kname in ("KEY_SLEFT", "KEY_SRIGHT"):
        f = flds[min(cursor, len(flds) - 1)]
        coarse = 1 if f["choices"] else max(1, (f["max"] - f["min"]) // 16)
        _nudge(-coarse if kname == "KEY_SLEFT" else coarse)
        return True
    if kname == "KEY_ENTER" or s in ("\r", "\n"):
        _entry_begin("value")
        return True
    if s == "s":
        element_slot = (element_slot + 1) % 4
        _mark_save()
        _status(f"element slot {element_slot + 1} (no burst on switch, VST-style)")
        return True
    if s == "f":
        filter_select = 1 - filter_select
        _mark_save()
        _status(f"filter bank {filter_select + 1}")
        return True
    if s == "d":
        _entry_begin("devnum")
        return True
    if s == "x":
        _send_messages(DEV.panel_cancel(device_number), "panel Cancel")
        _status("panel Cancel sent")
        return True
    if s == "v":
        _send_messages(DEV.panel_exit(device_number), "panel Exit")
        _status("panel Exit sent")
        return True
    if s == "X":
        _cancel_burst_thread()
        return True
    if s == ",":
        channel = max(1, channel - 1)
        note_target_channel = channel
        _mark_save()
        return True
    if s == ".":
        channel = min(16, channel + 1)
        note_target_channel = channel
        _mark_save()
        return True
    if s == "L":
        _arm_learn()
        return True
    if s == "M":
        _map_learn()
        return True
    if s == "U":
        _map_unbind()
        return True
    return False


def _bar(f, v, width=8):
    span = f["max"] - f["min"]
    if span <= 0:
        return "-" * width
    fill = round((v - f["min"]) / span * width)
    return "#" * fill + "-" * (width - fill)


def _build_lines(cols):
    flds = _fields()
    cur = min(cursor, len(flds) - 1)
    g = GROUPS[group_idx % len(GROUPS)]
    lines = [
        f"--- TG77  ch{channel:02d} dev{device_number:02d}  slot {element_slot+1}"
        f"  fltbank {filter_select+1}  [{group_idx+1}/{len(GROUPS)}] {g} ---",
        f"out: {'ok' if out_port else out_err or '(closed)'}   knob: {_knob_status()}   {_map_text()}",
        "",
    ]
    # two-column layout: 40 rows of specs don't fit one column on the CRT
    rows = []
    bars = []
    for i, f in enumerate(flds):
        v = _get_value(f["family"], f["sid"])
        mark = ">" if i == cur else " "
        if f["choices"]:
            rows.append(f" {mark} {f['name']:<22.22s} <{_display(f, v):>8.8s}>")
            if ctrlgfx.is_wave_field(f["name"], f["choices"]):
                bars.append({"wave": True, "labels": list(f["choices"]), "selected": v - f["min"],
                             "focused": i == cur, "title": f"{f['name']}: {_display(f, v)}"})
            else:
                bars.append(None)
        else:
            rows.append(f" {mark} {f['name']:<22.22s} [{_bar(f, v)}]{_display(f, v):>4s}")
            span = f["max"] - f["min"]
            bars.append({"frac": (v - f["min"]) / span if span else 0.0,
                         "bipolar": f["min"] < 0, "focused": i == cur})
    half = (len(rows) + 1) // 2
    width = max(len(r) for r in rows) + 2 if rows else 0
    _gfx.clear()
    row0 = len(lines)
    for i in range(half):
        left = rows[i]
        right = rows[i + half] if i + half < len(rows) else ""
        lines.append(f"{left:<{width}s}{right}")
        for j, coloff in ((i, 0), (i + half, width)):
            if j < len(bars) and bars[j]:
                b = bars[j]
                if b.get("wave"):
                    _gfx.append({"kind": "wavestrip", "row": row0 + i, "col": coloff + 26, "cols": 11,
                                 "rows": 1, "labels": b["labels"], "selected": b["selected"],
                                 "focused": b["focused"]})
                    if b["focused"] and g not in _OP_GROUPS:
                        _gfx.append({"kind": "wavestrip", "row": row0 + half + 1, "col": 2, "cols": 60,
                                     "rows": 5, "labels": b["labels"], "selected": b["selected"],
                                     "focused": True, "expand": 1.8, "title": b["title"]})
                else:
                    _gfx.append({"kind": "bar", "row": row0 + i, "col": coloff + 26, "cols": 10, **b})
    if g in _OP_GROUPS:
        # AFM EG: HT hold at L0, rates R1..R4 to L1..L4, release RR1/RR2 to RL1/RL2
        gv = lambda sid: _get_value("op", sid) / 63.0
        rate_w = lambda r: 0.04 + (1.0 - r) * 0.22
        segs = [(0.03 + gv("AfmEgHt") * 0.15, gv("AfmEgL0"), gv("AfmEgL0")),
                (rate_w(gv("AfmEgR1")), gv("AfmEgL0"), gv("AfmEgL1")),
                (rate_w(gv("AfmEgR2")), gv("AfmEgL1"), gv("AfmEgL2")),
                (rate_w(gv("AfmEgR3")), gv("AfmEgL2"), gv("AfmEgL3")),
                (rate_w(gv("AfmEgR4")), gv("AfmEgL3"), gv("AfmEgL4")),
                (0.12, gv("AfmEgL4"), gv("AfmEgL4")),
                (rate_w(gv("AfmEgRr1")), gv("AfmEgL4"), gv("AfmEgRl1")),
                (rate_w(gv("AfmEgRr2")), gv("AfmEgRl1"), gv("AfmEgRl2"))]
        _gfx.append({"kind": "env", "row": row0 + half + 1, "col": 2, "cols": 60, "rows": 8,
                     "segments": segs, "label": f"{g} AFM EG"})
    lines.extend([""] * 9)   # room for the EG plot / wave panel below the columns
    lines.append("")
    if entry_mode == "value":
        f = flds[cur]
        lines.append(f" ENTER {f['name']} ({f['min']}..{f['max']}): {entry_buf}_")
    elif entry_mode == "devnum":
        lines.append(f" DEVICE NUMBER (0-15): {entry_buf}_")
    elif status_msg and time.time() - status_time < 6.0:
        lines.append(f" {status_msg}")
    else:
        lines.append("")
    lines.append(" [/]:group s:slot f:fltbank d:devnum x/v/X:panel Enter:type L:learn M/U:map ,/.:ch")   # key legend: always on screen
    if last_tx:
        lines.append(f" tx: {last_tx}"[: max(20, cols - 1)])
    return lines


def on_tick(state):
    _flush_save()


def draw(state):
    _flush_save()
    cols = state["cols"]
    y0 = state.get("y_offset", 3)
    for idx, line in enumerate(_build_lines(cols)):
        draw_line(y0 + idx, line[:cols])


def build_widget(state):
    lines = _build_lines(int(state.get("cols", 100)))
    return CanvasWidget(page_id=PAGE_ID, page_name=PAGE_NAME, lines=lines,
                        painters=(ctrlgfx.make_painter(_gfx),))
