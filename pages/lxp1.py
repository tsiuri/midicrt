# pages/lxp1.py — Lexicon LXP-1 controller page
#
# Sends Nibblized Parameter Adjust sysex (F0 06 02 5n pp d d d d F7) built from
# the per-algorithm parameter tables in the LXP-1 owner's manual (rev 1.2,
# chapter 4).  Protocol notes: docs/lxp1-protocol.md.
#
# Controls (page keys; digits require page-lock, which numeric entry manages
# automatically):
#   Up/Down            move field cursor
#   Left/Right         nudge field one step (transmits)
#   Shift-Left/Right   coarse nudge (~5% of range)
#   Enter              typed entry in display units; Enter commits, Esc cancels
#   g / G              next / previous factory preset (setup select, param 64)
#   c                  channel-set burst: hold the unit's MIDI button while
#                      this arrives to lock the LXP-1 to the page's channel
#   S / R              store register / recall setup (typed number; R: 0-127
#                      registers, 128-144 factory presets 0-15)
#   , / .              MIDI channel down / up
#   L                  arm knob-learn on the knobctl plugin (next CC binds)
#   E                  pull the active setup dump into the GUI (needs the
#                      unit's MIDI jack jumpered as OUT + cabled to UX16 IN)

BACKGROUND = False
PAGE_ID = 18
PAGE_NAME = "LXP-1 Ctrl"

import threading
import time

import mido

from midicrt import draw_line
from configutil import load_section, save_section
from ui.model import PageLinesWidget


# ---------------------------------------------------------------------------
# Parameter tables — LXP-1 manual pages 4-11 .. 4-22.
# (num, name, bipolar, steps, dmin, dmax, unit, approx_display)
# Display ranges marked approx=True are linear interpolations of a taper the
# manual doesn't fully specify; the transmitted value is exact regardless.
# ---------------------------------------------------------------------------


# Tables + message builders live in devices/lxp1.py (shared with the web
# control surface and future ports).  NOTE: writing Program ID (param 65) does
# NOT load a program — verified dead on the real unit 2026-08-24.
from devices.lxp1 import (
    ALGORITHMS, PRESETS, PARAM_SETUP, EVENT_STORE_REGISTER, P,
    fields_for_program, step_to_value16, param_adjust_sysex, event_sysex,
    factory_default_steps, request_active_setup_sysex, decode_setup_dump,
    value16_to_step,
)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_cfg = {}
try:
    _cfg = load_section("lxp1") or {}
except Exception:
    _cfg = {}

channel = int(_cfg.get("channel", 1))            # 1-16
program = int(_cfg.get("program", 1))            # algorithm 1-8
preset = int(_cfg.get("preset", 0))              # factory preset 0-15
# values[str(pgm)][str(param_num)] = last-sent step (device state is write-only
# from our side for now, so unknown fields show "--")
values = _cfg.get("values", {}) if isinstance(_cfg.get("values"), dict) else {}
# a fresh program with no known values shows the manual's factory defaults
def _seed_if_empty():
    if not values.get(str(program)):
        values[str(program)] = {str(k): v for k, v in factory_default_steps(preset).items()}
output_hints = _cfg.get("output_hints", ["UX16", "USB MIDI", "MIDI 1"])
# sysex parameter-adjust class: "packed" (0x2n, the format the PC1600 template
# provably used on real units) or "nibble" (0x5n, manual-documented)
param_class = str(_cfg.get("param_class", "packed"))

# expose for plugins/knobctl.py: notes from the little keyboard follow the page
note_target_channel = channel
_seed_if_empty()

cursor = 0
entry_mode = None       # None | "value" | "store" | "recall"
entry_buf = ""
_entry_prev_lock = None
status_msg = ""
status_time = 0.0
last_tx = ""
out_port = None
out_err = ""
_save_pending = 0.0



def _fields():
    return fields_for_program(program)


def _mark_save():
    global _save_pending
    _save_pending = time.time() + 2.0


def _flush_save():
    global _save_pending
    if _save_pending and time.time() >= _save_pending:
        _save_pending = 0.0
        try:
            save_section("lxp1", {
                "channel": channel,
                "program": program,
                "preset": preset,
                "param_class": param_class,
                "values": values,
                "output_hints": output_hints,
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


def _send_sysex(payload):
    """payload = bytes between F0 and F7 (excluded)."""
    global last_tx
    if not _ensure_out():
        _status(f"TX FAILED: {out_err}")
        return False
    try:
        out_port.send(mido.Message("sysex", data=list(payload)))
        last_tx = "F0 " + " ".join(f"{b:02X}" for b in payload) + " F7"
        return True
    except Exception as exc:
        _status(f"TX FAILED: {exc}")
        return False


def _send_param(param_num, value16, klass=None, ch=None):
    return _send_sysex(param_adjust_sysex(
        param_num, value16, channel if ch is None else ch,
        param_class if klass is None else klass))


def _send_event(event, p):
    return _send_sysex(event_sysex(event, p, channel))


# ---------------------------------------------------------------------------
# Value model
# ---------------------------------------------------------------------------

_step_to_value16 = step_to_value16


def _get_step(param):
    return values.get(str(program), {}).get(str(param["num"]))


def _set_step(param, step, send=True):
    step = max(0, min(param["steps"] - 1, int(step)))
    values.setdefault(str(program), {})[str(param["num"])] = step
    _mark_save()
    if send:
        _send_param(param["num"], _step_to_value16(param, step))
    return step


def _display_value(param, step):
    if step is None:
        return "--"
    steps = param["steps"]
    frac = 0.0 if steps <= 1 else step / (steps - 1)
    val = param["dmin"] + frac * (param["dmax"] - param["dmin"])
    prefix = "~" if param["approx"] else ""
    if abs(param["dmax"] - param["dmin"]) >= 50 and not (-10 < val < 10):
        num = f"{val:.0f}"
    else:
        num = f"{val:.2f}".rstrip("0").rstrip(".")
    return f"{prefix}{num}{param['unit']}"


def _display_to_step(param, disp):
    lo, hi = param["dmin"], param["dmax"]
    if hi == lo:
        return 0
    frac = (float(disp) - lo) / (hi - lo)
    frac = max(0.0, min(1.0, frac))
    return round(frac * (param["steps"] - 1))


def _nudge(delta):
    flds = _fields()
    if not flds:
        return
    param = flds[min(cursor, len(flds) - 1)]
    cur = _get_step(param)
    if cur is None:
        # unknown device state: start from the middle so the first touch is sane
        cur = (param["steps"] - 1) // 2
        new = cur
    else:
        new = cur + delta
    step = _set_step(param, new)
    _status(f"{param['name']} -> step {step}/{param['steps']-1} = {_display_value(param, step)}")


def on_knob_delta(delta):
    """Fallback for endless-encoder knobs (see knobctl)."""
    _nudge(int(delta))


def on_knob_value(value):
    """Absolute knob position from knobctl: CC 0..127 spans the focused
    field's entire range."""
    flds = _fields()
    if not flds:
        return
    param = flds[min(cursor, len(flds) - 1)]
    frac = max(0, min(127, int(value))) / 127.0
    step = round(frac * (param["steps"] - 1))
    if step == _get_step(param):
        return
    step = _set_step(param, step)
    _status(f"{param['name']} -> step {step}/{param['steps']-1} = {_display_value(param, step)}")


# ---------------------------------------------------------------------------
# Numeric entry (manages the global page-lock so digits reach us)
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
    global program
    mode, buf = entry_mode, entry_buf.strip()
    _entry_end()
    if not buf:
        return
    try:
        num = float(buf)
    except ValueError:
        _status(f"bad number: {buf!r}")
        return
    if mode == "value":
        flds = _fields()
        param = flds[min(cursor, len(flds) - 1)]
        step = _display_to_step(param, num)
        step = _set_step(param, step)
        _status(f"{param['name']} = {_display_value(param, step)} (step {step})")
    elif mode == "store":
        reg = max(0, min(127, int(num)))
        if _send_event(EVENT_STORE_REGISTER, reg):
            _status(f"stored edit state to register {reg}")
    elif mode == "recall":
        setup = max(0, min(144, int(num)))
        if setup < 128:
            ok = _send_program_change(setup)   # documented register load
            label = f"register {setup} (via Program Change)"
        else:
            ok = _send_param(PARAM_SETUP, setup)
            label = f"factory preset {setup-128}"
        if ok:
            values.pop(str(program), None)   # device state changed under us
            _mark_save()
            _status(f"recalled {label} (params now unknown)")


def _seed_factory_defaults():
    """Show the manual's documented factory values for the selected preset
    (knob params + FX level); everything else stays unknown until pulled."""
    values[str(program)] = {str(k): v for k, v in factory_default_steps(preset).items()}


def _set_preset(idx):
    global preset, program, cursor
    preset = idx % 16
    program = PRESETS[preset][1]
    cursor = 0
    _seed_factory_defaults()
    _mark_save()
    if _send_param(PARAM_SETUP, 128 + preset):
        _status(f"preset {preset}: {PRESETS[preset][0]} ({ALGORITHMS[program][0]}) — manual defaults shown")


def _send_program_change(pp):
    if not _ensure_out():
        _status(f"TX FAILED: {out_err}")
        return False
    global last_tx
    try:
        out_port.send(mido.Message("program_change", program=pp & 0x7F,
                                   channel=(channel - 1) & 0x0F))
        last_tx = f"PC {pp} ch{channel}"
        return True
    except Exception as exc:
        _status(f"TX FAILED: {exc}")
        return False


def _channel_set_burst():
    """Hold the LXP-1's front-panel MIDI button while this arrives and the
    unit locks itself to our channel (manual 3-2: any complete channel
    message; a note then pitch-bend breaks any running status)."""
    if not _ensure_out():
        _status(f"TX FAILED: {out_err}")
        return
    ch = (channel - 1) & 0x0F
    try:
        out_port.send(mido.Message("note_on", note=60, velocity=1, channel=ch))
        out_port.send(mido.Message("note_off", note=60, velocity=0, channel=ch))
        out_port.send(mido.Message("pitchwheel", pitch=0, channel=ch))
        _status(f"channel-set burst sent on ch{channel} (hold the MIDI button!)")
    except Exception as exc:
        _status(f"TX FAILED: {exc}")


_pull_active = False


def _apply_setup_dump(dec):
    global program, cursor
    pgm = dec["program"]
    if pgm not in ALGORITHMS:
        v0 = dec["values16"].get(0, 0)
        _status(f"PULL: unit reports program ID {pgm} / p0=0x{v0:04X} — INVALID."
                " Memory corrupt (battery?) — do the front-panel factory reset")
        return
    program = pgm
    cursor = 0
    steps = {}
    for p in fields_for_program(program):
        v16 = dec["values16"].get(p["num"])
        if v16 is not None:
            steps[str(p["num"])] = value16_to_step(p, v16)
    values[str(program)] = steps
    _mark_save()
    src = f"register {dec['register']}" if dec.get("register") is not None else "active setup"
    _status(f"PULLED {src}: {dec.get('name') or '(unnamed)'} — {ALGORITHMS[program][0]}")


def _pull_worker():
    global _pull_active
    inp = None
    try:
        target = None
        names = list(mido.get_input_names())
        for hint in output_hints:
            hl = str(hint).lower()
            for n in names:
                if hl in n.lower():
                    target = n
                    break
            if target:
                break
        if target is None:
            _status("PULL: no MIDI input matching output hints")
            return
        inp = mido.open_input(target)
        for _ in inp.iter_pending():
            pass
        if not _send_sysex(request_active_setup_sysex(channel)):
            return
        deadline = time.time() + 3.0
        while time.time() < deadline:
            for msg in inp.iter_pending():
                if msg.type != "sysex":
                    continue
                dec = decode_setup_dump(msg.data)
                if dec is not None:
                    _apply_setup_dump(dec)
                    return
            time.sleep(0.02)
        _status("PULL: no dump in 3s — LXP-1 jack must be jumpered as OUT and cabled to UX16 IN")
    except Exception as exc:
        _status(f"PULL failed: {exc}")
    finally:
        if inp is not None:
            try:
                inp.close()
            except Exception:
                pass
        _pull_active = False


def _start_pull():
    global _pull_active
    if _pull_active:
        _status("pull already in progress")
        return
    _pull_active = True
    _status("PULL: requesting active setup...")
    threading.Thread(target=_pull_worker, daemon=True).start()


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
# Key handling
# ---------------------------------------------------------------------------

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
        if s and (s.isdigit() or (s in "-." and s not in entry_buf)):
            entry_buf += s
            return True
        return True  # swallow everything else while entering

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
    if kname in ("KEY_SLEFT", "KEY_SRIGHT"):
        param = flds[min(cursor, len(flds) - 1)]
        coarse = max(1, param["steps"] // 20)
        _nudge(-coarse if kname == "KEY_SLEFT" else coarse)
        return True
    if kname == "KEY_ENTER" or s in ("\r", "\n"):
        _entry_begin("value")
        return True

    if s == "g":
        _set_preset(preset + 1)
        return True
    if s == "G":
        _set_preset(preset - 1)
        return True
    if s == "c":
        _channel_set_burst()
        return True
    if s == "S":
        _entry_begin("store")
        return True
    if s == "R":
        _entry_begin("recall")
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
    if s == "E":
        _start_pull()
        return True
    return False


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _bar(param, step, width=12):
    if step is None:
        return "." * width
    frac = 0.0 if param["steps"] <= 1 else step / (param["steps"] - 1)
    fill = round(frac * width)
    return "#" * fill + "-" * (width - fill)


def _build_lines(cols):
    flds = _fields()
    cur = min(cursor, len(flds) - 1)
    pgm_name = ALGORITHMS[program][0]
    lines = [
        f"--- LXP-1  ch{channel:02d} {param_class}  preset {preset}: {PRESETS[preset][0]}  alg: {pgm_name} ---",
        f"out: {'ok' if out_port else out_err or '(closed)'}   knob: {_knob_status()}",
        "",
    ]
    for i, param in enumerate(flds):
        step = _get_step(param)
        mark = ">" if i == cur else " "
        steptxt = "---" if step is None else f"{step:4d}"
        lines.append(
            f" {mark} {param['name']:<15s} [{_bar(param, step)}] "
            f"{steptxt}/{param['steps']-1:<4d} {_display_value(param, step):>10s}"
            f"{'  (bi)' if param['bipolar'] else ''}"
        )
    lines.append("")
    if entry_mode == "value":
        param = flds[cur]
        lines.append(f" ENTER {param['name']} ({param['dmin']:g}..{param['dmax']:g}{param['unit']}): {entry_buf}_")
    elif entry_mode == "store":
        lines.append(f" STORE to register (0-127): {entry_buf}_")
    elif entry_mode == "recall":
        lines.append(f" RECALL setup (0-127 reg, 128-144 preset): {entry_buf}_")
    elif status_msg and time.time() - status_time < 6.0:
        lines.append(f" {status_msg}")
    else:
        lines.append(" arrows:nudge Enter:type g:preset E:pull S/R:store/recall L:learn ,/.:ch c:unit-ch")
    if last_tx:
        lines.append(f" tx: {last_tx}"[: max(20, cols - 1)])
    return lines


def draw(state):
    _flush_save()
    cols = state["cols"]
    y0 = state.get("y_offset", 3)
    for idx, line in enumerate(_build_lines(cols)):
        draw_line(y0 + idx, line[:cols])


def update(state):
    _flush_save()


def build_widget(state):
    return PageLinesWidget(page_id=PAGE_ID, page_name=PAGE_NAME,
                           lines=_build_lines(int(state.get("cols", 100))))
