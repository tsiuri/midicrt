# pages/bassstation.py — Novation Bass Station Rack controller page
#
# Device definition: devices/bassstation.py.  The rack only exposes filter +
# envelope (+ mod/breath/volume) over MIDI CC — oscillator/LFO are
# front-panel only, so this page is the complete remote surface the hardware
# offers.  Same contract as the other controller pages:
#   Up/Down arrows     field cursor      Left/Right  nudge (Shift coarse)
#   Enter              typed entry 0-127
#   p                  typed program 0-99 (factory names shown for 00-39)
#   , / .              MIDI channel      L  knob learn (absolute full-range)
#
# Reachable via the Esc page menu or +/- page cycling (the shifted-digit nav
# keys ran out at page 20).

BACKGROUND = False
PAGE_ID = 21
PAGE_NAME = "BassStation"

import time

import mido

from midicrt import draw_line
from configutil import load_section, save_section
from ui.model import PageLinesWidget
from devices import bassstation as DEV

_cfg = {}
try:
    _cfg = load_section("bassstation") or {}
except Exception:
    _cfg = {}

channel = int(_cfg.get("channel", DEV.DEFAULT_CHANNEL))
program = int(_cfg.get("program", 0))
values = _cfg.get("values", {}) if isinstance(_cfg.get("values"), dict) else {}
output_hints = _cfg.get("output_hints", ["UX16", "USB MIDI", "MIDI 1"])

note_target_channel = channel

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


def _fields():
    return [{"id": pid, "name": f"{g}: {label}", "cc": cc}
            for g, pid, label, cc in DEV.PARAMS]


def _mark_save():
    global _save_pending
    _save_pending = time.time() + 2.0


def _flush_save():
    global _save_pending
    if _save_pending and time.time() >= _save_pending:
        _save_pending = 0.0
        try:
            save_section("bassstation", {
                "channel": channel, "program": program,
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


def _send_cc(cc, value):
    global last_tx
    if not _ensure_out():
        _status(f"TX FAILED: {out_err}")
        return False
    try:
        out_port.send(mido.Message("control_change", control=cc, value=value,
                                   channel=(channel - 1) & 0x0F))
        last_tx = f"CC{cc}={value} ch{channel}"
        return True
    except Exception as exc:
        _status(f"TX FAILED: {exc}")
        return False


def _send_pc(pp):
    global last_tx
    if not _ensure_out():
        _status(f"TX FAILED: {out_err}")
        return False
    try:
        out_port.send(mido.Message("program_change", program=pp & 0x7F,
                                   channel=(channel - 1) & 0x0F))
        last_tx = f"PC {pp} ch{channel}"
        return True
    except Exception as exc:
        _status(f"TX FAILED: {exc}")
        return False


def _get_value(f):
    return int(values.get(f["id"], 64))


def _set_value(f, v, send=True):
    v = max(0, min(127, int(v)))
    values[f["id"]] = v
    _mark_save()
    if send:
        _send_cc(f["cc"], v)
    return v


def _nudge(delta):
    flds = _fields()
    f = flds[min(cursor, len(flds) - 1)]
    v = _set_value(f, _get_value(f) + delta)
    _status(f"{f['name']} = {v}")


def on_knob_value(value):
    flds = _fields()
    f = flds[min(cursor, len(flds) - 1)]
    v = max(0, min(127, int(value)))
    if v == _get_value(f):
        return
    v = _set_value(f, v)
    _status(f"{f['name']} = {v}")


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
    global program
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
        _status(f"{f['name']} = {v}")
    elif mode == "program":
        program = max(0, min(99, num))
        _mark_save()
        if _send_pc(program):
            _status(f"program {program:02d}: {DEV.program_name(program)}")


_CC_TO_ID = {cc: pid for _, pid, _, cc in DEV.PARAMS}


def on_device_message(msg):
    """devicesync plugin: the rack transmits CC 105-118 when its filter /
    envelope knobs move — keep the GUI in step."""
    if msg.type != "control_change" or msg.channel != (channel - 1):
        return
    pid = _CC_TO_ID.get(msg.control)
    if pid is None or msg.control < 105:
        return
    values[pid] = int(msg.value)
    _mark_save()
    _status(f"unit: {pid} -> {msg.value}")


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
    global cursor, channel, entry_buf, note_target_channel
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
        if s and s.isdigit():
            entry_buf += s
            return True
        return True

    flds = _fields()
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
    if kname == "KEY_SLEFT":
        _nudge(-8)
        return True
    if kname == "KEY_SRIGHT":
        _nudge(8)
        return True
    if kname == "KEY_ENTER" or s in ("\r", "\n"):
        _entry_begin("value")
        return True
    if s == "p":
        _entry_begin("program")
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
    return False


def _bar(v, width=14):
    fill = round(v / 127 * width)
    return "#" * fill + "-" * (width - fill)


def _build_lines(cols):
    flds = _fields()
    cur = min(cursor, len(flds) - 1)
    lines = [
        f"--- Bass Station Rack  ch{channel:02d}"
        f"  pgm {program:02d}: {DEV.program_name(program)} ---",
        f"out: {'ok' if out_port else out_err or '(closed)'}   knob: {_knob_status()}",
        "",
    ]
    last_group = None
    for i, f in enumerate(flds):
        group, label = f["name"].split(": ", 1)
        if group != last_group:
            if last_group is not None:
                lines.append("")
            lines.append(f"  == {group} ==")
            last_group = group
        v = _get_value(f)
        mark = ">" if i == cur else " "
        lines.append(f" {mark} {label:<12s} [{_bar(v)}] {v:3d}   (CC{f['cc']})")
    lines.append("")
    if entry_mode == "value":
        f = flds[cur]
        lines.append(f" ENTER {f['name']} (0-127): {entry_buf}_")
    elif entry_mode == "program":
        lines.append(f" PROGRAM (0-99, 00-39 factory): {entry_buf}_")
    elif status_msg and time.time() - status_time < 6.0:
        lines.append(f" {status_msg}")
    else:
        lines.append("")
    lines.append(" arrows:move/nudge Enter:type p:program L:learn ,/.:ch")   # key legend: always on screen
    if last_tx:
        lines.append(f" tx: {last_tx}"[: max(20, cols - 1)])
    return lines


def draw(state):
    _flush_save()
    cols = state["cols"]
    y0 = state.get("y_offset", 3)
    for idx, line in enumerate(_build_lines(cols)):
        draw_line(y0 + idx, line[:cols])


def build_widget(state):
    return PageLinesWidget(page_id=PAGE_ID, page_name=PAGE_NAME,
                           lines=_build_lines(int(state.get("cols", 100))))
