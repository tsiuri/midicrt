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

BACKGROUND = False
PAGE_ID = 18
PAGE_NAME = "LXP-1 Ctrl"

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

def P(num, name, bipolar, steps, dmin, dmax, unit, approx=False):
    return {
        "num": num, "name": name, "bipolar": bipolar, "steps": steps,
        "dmin": float(dmin), "dmax": float(dmax), "unit": unit, "approx": approx,
    }


_REVERB_PARAMS = [
    P(0, "Decay", False, 16, 0.6, 9.0, "s", approx=True),
    P(1, "Pre-Delay", False, 4096, 0.0, 262.0, "ms"),
    P(2, "Effects Level", False, 256, 0, 100, "%"),
    P(3, "Bass Multiply", True, 32, 0.3, 2.5, "x", approx=True),
    P(4, "Hi Freq Cut", False, 16, 321, 13800, "Hz", approx=True),
    P(5, "Size", False, 64, 8, 71, "m"),
    P(6, "PreDly Fdbk", True, 512, -99, 99, "%"),
    P(7, "Diffusion", False, 256, 0, 100, ""),
]

ALGORITHMS = {
    1: ("Rooms and Halls", _REVERB_PARAMS),
    2: ("Plates", _REVERB_PARAMS),
    3: ("Chorus 1 (Stereo Flange)", [
        P(0, "Negative Fdbk", False, 256, 0, 99, "%"),
        P(1, "Flange Depth", False, 256, 0.25, 8.0, "ms", approx=True),
        P(2, "Effects Level", False, 256, 0, 100, "%"),
        P(3, "Right Feedback", True, 512, -99, 99, "%"),
        P(4, "Right Delay", False, 128, 0, 1000, "ms"),
        P(5, "Shape", False, 8, 0, 7, ""),
        P(6, "Left Feedback", True, 512, -99, 99, "%"),
        P(7, "Left Delay", False, 128, 0, 1000, "ms"),
        P(8, "Rate", False, 16, 0, 15, ""),
    ]),
    4: ("Delay 2 (4-tap bounce)", [
        P(0, "Positive Fdbk", False, 256, 0, 100, "%", approx=True),
        P(1, "Ganged Delay", False, 256, 0, 100, "", approx=True),
        P(2, "Effects Level", False, 256, 0, 100, "%"),
        P(3, "Feedback", True, 512, -99, 99, "%"),
        P(4, "Left Delay", False, 256, 0, 100, "", approx=True),
        P(5, "Right Delay", False, 256, 0, 100, "", approx=True),
        P(7, "Hi Freq Cut", False, 16, 321, 13800, "Hz", approx=True),
        P(8, "Diffusion", False, 256, 0, 100, ""),
    ]),
    5: ("Chorus 2 (Chromatic Resonator)", [
        P(0, "Mstr Resonance", False, 64, 93, 99, "%", approx=True),
        P(1, "Fine Tuning", True, 128, -8, 7, "semi", approx=True),
        P(2, "Effects Level", False, 256, 0, 100, "%"),
        P(3, "Pre-Delay", False, 256, 0, 524, "ms", approx=True),
        P(4, "Lo Freq Cut", False, 256, 19.5, 13500, "Hz", approx=True),
        P(5, "Shimmer", False, 16, 0, 15, ""),
        P(6, "Resonance Fdbk", True, 64, -99, 99, "%"),
        P(7, "Richness", False, 16, 0, 120, "cents"),
        P(8, "Slope", True, 32, -15, 15, ""),
        P(9, "Tuning", True, 128, -64, 63, "1/8semi"),
    ]),
    6: ("Inverse", [
        P(0, "Size", False, 32, 1, 32, ""),
        P(2, "Effects Level", False, 256, 0, 100, "%"),
        P(4, "Hi Freq Cut", False, 16, 321, 13800, "Hz", approx=True),
        P(5, "Slope", False, 32, 1, 16, "", approx=True),
        P(6, "PreDly Fdbk", True, 512, -99, 99, "%"),
        P(7, "Diffusion", False, 256, 0, 100, ""),
        P(8, "Pre-Delay", False, 4096, 0, 262, "ms"),
    ]),
    7: ("Gated Reverb", [
        P(0, "Gate Time", False, 32, 150, 390, "ms"),
        P(2, "Effects Level", False, 256, 0, 100, "%"),
        P(4, "Hi Freq Cut", False, 16, 321, 13800, "Hz", approx=True),
        P(5, "Slope", False, 16, 1, 16, ""),
        P(6, "PreDly Fdbk", True, 512, -99, 99, "%"),
        P(7, "Diffusion", False, 256, 0, 100, ""),
        P(8, "Pre-Delay", False, 4096, 0, 262, "ms"),
    ]),
    8: ("Delay 1 (6-voice Chorus & Echo)", [
        P(0, "Feedback", False, 256, 0, 94, "%", approx=True),
        P(1, "Group Delay", False, 256, 0, 623, "ms", approx=True),
        P(2, "Effects Level", False, 256, 0, 100, "%"),
        P(3, "High Cut", False, 16, 321, 13800, "Hz", approx=True),
        P(4, "Delay 2 Spread", False, 128, 0, 1000, "ms"),
        P(5, "Delay 3 Spread", False, 128, 0, 1000, "ms"),
        P(6, "Delay 3 Fdbk", True, 512, -99, 99, "%"),
        P(7, "Diffusion", False, 256, 0, 100, ""),
        P(8, "Rate", False, 16, 0, 15, ""),
    ]),
}

_INPUT_LEVEL = P(10, "Input Level", False, 256, 0, 100, "%")

# The 16 factory presets in front-panel program-table order (manual ch.2) with
# the algorithm each runs.  Preset numbering 0-15 = this order is the manual's
# program-table order — believed to match setup numbers 128-144; verify by ear.
PRESETS = [
    ("Small 1", 1), ("Small 2", 1), ("Medium 1", 1), ("Medium 2", 1),
    ("Large 1", 1), ("Large 2", 1), ("Hall D", 1), ("Hall B", 1),
    ("Plate D", 2), ("Plate B", 2), ("Inverse", 6), ("Gate", 7),
    ("Chorus 1", 3), ("Chorus 2", 5), ("Delay 1", 8), ("Delay 2", 4),
]

# NOTE: writing Program ID (param 65) does NOT load a program — verified dead
# on the real unit 2026-08-24.  Loading happens via standard MIDI Program
# Change (registers 0-127) or Setup select param 64 (128+n = factory presets).
PARAM_SETUP = 64
EVENT_STORE_REGISTER = 0x70

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
output_hints = _cfg.get("output_hints", ["UX16", "USB MIDI", "MIDI 1"])

# expose for plugins/knobctl.py: notes from the little keyboard follow the page
note_target_channel = channel

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
    return list(ALGORITHMS[program][1]) + [_INPUT_LEVEL]


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


def _send_param(param_num, value16):
    n = (channel - 1) & 0x0F
    value16 = max(0, min(0xFFFF, int(value16)))
    payload = (
        0x06, 0x02, 0x50 | n, param_num & 0x7F,
        (value16 >> 12) & 0x0F, (value16 >> 8) & 0x0F,
        (value16 >> 4) & 0x0F, value16 & 0x0F,
    )
    return _send_sysex(payload)


def _send_event(event, p):
    n = (channel - 1) & 0x0F
    return _send_sysex((0x06, 0x02, 0x60 | n, event & 0x7F, p & 0x7F))


# ---------------------------------------------------------------------------
# Value model
# ---------------------------------------------------------------------------

def _step_to_value16(param, step):
    steps = param["steps"]
    frac = 0.0 if steps <= 1 else step / (steps - 1)
    if param["bipolar"]:
        return 0x4000 + round(frac * 0x7FFF)
    return 0x8000 + round(frac * 0x3FFF)


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


def _set_preset(idx):
    global preset, program, cursor
    preset = idx % 16
    program = PRESETS[preset][1]
    cursor = 0
    values.pop(str(program), None)   # preset load resets device params
    _mark_save()
    if _send_param(PARAM_SETUP, 128 + preset):
        _status(f"preset {preset}: {PRESETS[preset][0]} ({ALGORITHMS[program][0]})")


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
        f"--- LXP-1  ch{channel:02d}  preset {preset}: {PRESETS[preset][0]}  alg: {pgm_name} ---",
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
        lines.append(" arrows:nudge Enter:type g:preset S/R:store/recall L:learn ,/.:ch c:set-unit-ch")
    if last_tx:
        lines.append(f" tx: {last_tx}"[: max(20, cols - 1)])
    return lines


def draw(state):
    cols = state["cols"]
    y0 = state.get("y_offset", 3)
    for idx, line in enumerate(_build_lines(cols)):
        draw_line(y0 + idx, line[:cols])


def update(state):
    _flush_save()


def build_widget(state):
    return PageLinesWidget(page_id=PAGE_ID, page_name=PAGE_NAME,
                           lines=_build_lines(int(state.get("cols", 100))))
