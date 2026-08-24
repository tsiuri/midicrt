# pages/matrix1000.py — Oberheim Matrix-1000 controller page
#
# Device definition + message builders: devices/matrix1000.py (ported from the
# Matrix1000NRPNController VST). Same interaction contract as the LXP-1 page:
#   [ / ]              previous / next parameter group
#   Up/Down            move field cursor
#   Left/Right         nudge one step (choice fields cycle); Shift = coarse
#   Enter              typed entry in actual units (negatives fine)
#   b / p              typed bank (0-9) / program (0-99) select
#   W                  store edit buffer -> selected bank/program (typed
#                      program doubles as confirmation)
#   E                  request edit buffer dump (response parsing TODO)
#   , / .              MIDI channel down / up
#   L                  arm knob-learn (knobctl); knob = absolute full range

BACKGROUND = False
PAGE_ID = 19
PAGE_NAME = "Matrix-1000"

import time

import mido

from midicrt import draw_line
from configutil import load_section, save_section
from ui.model import PageLinesWidget
from devices import matrix1000 as DEV

MOD_GROUP = "Mod Matrix"

_cfg = {}
try:
    _cfg = load_section("matrix1000") or {}
except Exception:
    _cfg = {}

channel = int(_cfg.get("channel", DEV.DEFAULT_CHANNEL))   # 1-16
bank = int(_cfg.get("bank", 0))                            # 0-9
program = int(_cfg.get("program", 0))                      # 0-99
values = _cfg.get("values", {}) if isinstance(_cfg.get("values"), dict) else {}
# mod matrix shadow: {"0": [src, amt, dst], ...}
mod = _cfg.get("mod", {}) if isinstance(_cfg.get("mod"), dict) else {}
output_hints = _cfg.get("output_hints", ["UX16", "USB MIDI", "MIDI 1"])

note_target_channel = channel

GROUPS = DEV.groups() + [MOD_GROUP]
group_idx = 0
cursor = 0
entry_mode = None      # None | "value" | "bank" | "program" | "store"
entry_buf = ""
_entry_prev_lock = None
status_msg = ""
status_time = 0.0
last_tx = ""
out_port = None
out_err = ""
_save_pending = 0.0


# ---------------------------------------------------------------------------
# Field model.  Regular fields wrap a DEV.PARAMS row; mod-matrix fields are
# synthesized (slot, role) triples.
# ---------------------------------------------------------------------------

def _param_field(row):
    g, pid, name, num, mn, mx, dflt, ck = row
    return {
        "kind": "param", "name": name, "num": num,
        "min": mn, "max": mx, "default": dflt,
        "choices": DEV.CHOICES.get(ck) if ck else None,
    }


def _mod_fields():
    out = []
    for slot in range(DEV.MOD_SLOTS):
        out.append({"kind": "mod", "slot": slot, "role": 0, "name": f"M{slot+1} Source",
                    "min": 0, "max": len(DEV.MOD_SOURCES) - 1, "default": 0,
                    "choices": DEV.MOD_SOURCES})
        out.append({"kind": "mod", "slot": slot, "role": 1, "name": f"M{slot+1} Amount",
                    "min": -63, "max": 63, "default": 0, "choices": None})
        out.append({"kind": "mod", "slot": slot, "role": 2, "name": f"M{slot+1} Dest",
                    "min": 0, "max": len(DEV.MOD_DESTS) - 1, "default": 0,
                    "choices": DEV.MOD_DESTS})
    return out


_MOD_FIELDS = _mod_fields()


def _fields():
    g = GROUPS[group_idx % len(GROUPS)]
    if g == MOD_GROUP:
        return _MOD_FIELDS
    return [_param_field(r) for r in DEV.params_in_group(g)]


# ---------------------------------------------------------------------------
# Persistence / status
# ---------------------------------------------------------------------------

def _mark_save():
    global _save_pending
    _save_pending = time.time() + 2.0


def _flush_save():
    global _save_pending
    if _save_pending and time.time() >= _save_pending:
        _save_pending = 0.0
        try:
            save_section("matrix1000", {
                "channel": channel, "bank": bank, "program": program,
                "values": values, "mod": mod, "output_hints": output_hints,
            })
        except Exception:
            pass


def _status(text):
    global status_msg, status_time
    status_msg = text
    status_time = time.time()


# ---------------------------------------------------------------------------
# MIDI out
# ---------------------------------------------------------------------------

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


def _tx(desc):
    global last_tx
    last_tx = desc


def _send_nrpn(num, value):
    if not _ensure_out():
        _status(f"TX FAILED: {out_err}")
        return False
    try:
        for cc, val in DEV.nrpn_cc_messages(num, value, channel):
            out_port.send(mido.Message("control_change", control=cc, value=val,
                                       channel=(channel - 1) & 0x0F))
        _tx(f"NRPN p{num}={value} ch{channel}")
        return True
    except Exception as exc:
        _status(f"TX FAILED: {exc}")
        return False


def _send_sysex(payload, desc):
    if not _ensure_out():
        _status(f"TX FAILED: {out_err}")
        return False
    try:
        out_port.send(mido.Message("sysex", data=list(payload)))
        _tx(desc)
        return True
    except Exception as exc:
        _status(f"TX FAILED: {exc}")
        return False


def _send_pc(pp):
    if not _ensure_out():
        _status(f"TX FAILED: {out_err}")
        return False
    try:
        out_port.send(mido.Message("program_change", program=pp & 0x7F,
                                   channel=(channel - 1) & 0x0F))
        _tx(f"PC {pp} ch{channel}")
        return True
    except Exception as exc:
        _status(f"TX FAILED: {exc}")
        return False


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------

def _get_value(f):
    if f["kind"] == "mod":
        row = mod.get(str(f["slot"]), [0, 0, 0])
        return int(row[f["role"]])
    return int(values.get(str(f["num"]), f["default"]))


def _display(f, v):
    if f["choices"]:
        i = v - f["min"]
        if 0 <= i < len(f["choices"]):
            return f["choices"][i]
        return str(v)
    return f"{v:+d}" if f["min"] < 0 else str(v)


def _set_value(f, v, send=True):
    v = max(f["min"], min(f["max"], int(v)))
    if f["kind"] == "mod":
        row = list(mod.get(str(f["slot"]), [0, 0, 0]))
        row[f["role"]] = v
        mod[str(f["slot"])] = row
        _mark_save()
        if send:
            _send_sysex(DEV.mod_matrix_sysex(f["slot"], row[0], row[1], row[2]),
                        f"MOD slot{f['slot']+1} {row}")
    else:
        values[str(f["num"])] = v
        _mark_save()
        if send:
            _send_nrpn(f["num"], v)
    return v


def _nudge(delta):
    flds = _fields()
    f = flds[min(cursor, len(flds) - 1)]
    v = _set_value(f, _get_value(f) + delta)
    _status(f"{f['name']} = {_display(f, v)}")


def on_knob_value(value):
    """Absolute knob: CC 0..127 spans the focused field's range."""
    flds = _fields()
    f = flds[min(cursor, len(flds) - 1)]
    frac = max(0, min(127, int(value))) / 127.0
    v = round(f["min"] + frac * (f["max"] - f["min"]))
    if v == _get_value(f):
        return
    v = _set_value(f, v)
    _status(f"{f['name']} = {_display(f, v)}")


def on_knob_delta(delta):
    _nudge(int(delta))


# ---------------------------------------------------------------------------
# Entry (auto page-lock like the LXP-1 page)
# ---------------------------------------------------------------------------

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
    global bank, program
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
    elif mode == "bank":
        bank = max(0, min(9, num))
        _mark_save()
        ok = _send_sysex(DEV.bank_select_sysex(bank), f"BANK {bank}")
        if ok:
            _send_sysex(DEV.bank_unlock_sysex(), f"BANK {bank}+unlock")
            _status(f"bank {bank} selected (+unlock)")
    elif mode == "program":
        program = max(0, min(99, num))
        _mark_save()
        if _send_pc(program):
            values.clear()   # patch change: shadow state now unknown
            _mark_save()
            _status(f"program {program} loaded (params reset to defaults display)")
    elif mode == "store":
        program = max(0, min(99, num))
        _mark_save()
        if _send_sysex(DEV.store_edit_buffer_sysex(program, bank),
                       f"STORE b{bank} p{program}"):
            _send_sysex(DEV.bank_unlock_sysex(), f"STORE b{bank} p{program}+unlock")
            _status(f"stored edit buffer to bank {bank} program {program}")


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


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

def keypress(key):
    global cursor, group_idx, channel, entry_buf, note_target_channel
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
    if s == "b":
        _entry_begin("bank")
        return True
    if s == "p":
        _entry_begin("program")
        return True
    if s == "W":
        _entry_begin("store")
        return True
    if s == "E":
        if _send_sysex(DEV.request_edit_buffer_sysex(), "REQ edit buffer"):
            _status("edit-buffer dump requested (see event log / sysex.log)")
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


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _bar(f, v, width=10):
    span = f["max"] - f["min"]
    if span <= 0:
        return "-" * width
    frac = (v - f["min"]) / span
    fill = round(frac * width)
    return "#" * fill + "-" * (width - fill)


def _build_lines(cols):
    flds = _fields()
    cur = min(cursor, len(flds) - 1)
    g = GROUPS[group_idx % len(GROUPS)]
    lines = [
        f"--- Matrix-1000  ch{channel:02d}  bank {bank}  prog {program:02d}"
        f"  [{group_idx+1}/{len(GROUPS)}] {g} ---",
        f"out: {'ok' if out_port else out_err or '(closed)'}   knob: {_knob_status()}",
        "",
    ]
    for i, f in enumerate(flds):
        v = _get_value(f)
        mark = ">" if i == cur else " "
        if f["choices"]:
            lines.append(f" {mark} {f['name']:<24s} <{_display(f, v)}>")
        else:
            lines.append(f" {mark} {f['name']:<24s} [{_bar(f, v)}] {_display(f, v):>6s}"
                         f"  ({f['min']}..{f['max']})")
    lines.append("")
    if entry_mode == "value":
        f = flds[cur]
        lines.append(f" ENTER {f['name']} ({f['min']}..{f['max']}): {entry_buf}_")
    elif entry_mode == "bank":
        lines.append(f" BANK (0-9): {entry_buf}_")
    elif entry_mode == "program":
        lines.append(f" PROGRAM (0-99): {entry_buf}_")
    elif entry_mode == "store":
        lines.append(f" STORE edit buffer -> bank {bank}, program (0-99): {entry_buf}_")
    elif status_msg and time.time() - status_time < 6.0:
        lines.append(f" {status_msg}")
    else:
        lines.append(" [/]:group arrows:nudge Enter:type b/p:bank/prog W:store E:pull L:learn ,/.:ch")
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
