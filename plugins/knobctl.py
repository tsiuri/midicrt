# plugins/knobctl.py — M-VAVE SMK-25 mini (BLE MIDI keyboard, 1 knob) router
#
# Opens its OWN mido input for the little control keyboard — deliberately
# separate from midicrt's main monitor input, so rack traffic can never be
# mistaken for the knob (no false learns) and forwarded notes can never loop
# back out (input here is only ever the SMK-25; output is the rack interface).
#
# Behavior:
#   * knob learn — a page calls arm_learn(); the next CC seen here becomes the
#     bound knob (persisted to config section "knobctl").
#   * bound knob CC -> current page's on_knob_delta(steps) if it defines one.
#   * notes (+ pitchbend/aftertouch/sustain etc.) are forwarded to the rack
#     output, rewritten to the current page's note_target_channel (1-16) when
#     the page declares one — "the keyboard plays whatever I'm pointed at".
#   * sticky target — the last controller page's channel keeps receiving notes
#     on neutral pages (persisted as "sticky_channel").
#   * keyboard-control mode — knob at max, then lo-C x3, hi-C x3, lo-C (within
#     5 s) enters a mode where lo/hi step pages and other notes are swallowed;
#     hold lo+hi together 1 s to leave. Footer shows " BT CTRL " in reverse.
#   * footer indicator — indicator() -> (text, reverse): "BT ch3 *" with a
#     120 ms flash on ANY received message; "BT off" when disconnected.
#     State machine + rules: plugins/knobctl_control.py (unit-tested).
#
# The BLE keyboard comes and goes; a background thread rescans for the input
# every few seconds and survives disconnects.

import threading
import time

import mido

from configutil import load_section, save_section
from plugins.knobctl_control import KeyboardControl

_cfg = {}
try:
    _cfg = load_section("knobctl") or {}
except Exception:
    _cfg = {}

# "Midi Through Port-0" is the PipeWire->ALSA landing zone for the BLE
# keyboard (see tools/smk25-pair.sh); Port-1 belongs to netmidi — never add it.
input_hints = _cfg.get("input_hints", ["SMK", "M-VAVE", "MVAVE", "SMK25", "Midi Through Port-0"])
# (the same hints match the keyboard's USB-MIDI name when it is cabled to the Pi —
#  preferred over BLE: bluez's BLE-MIDI parser garbles multi-message packets)
output_hints = _cfg.get("output_hints", ["UX16", "USB MIDI", "MIDI 1"])
knob_cc = _cfg.get("knob_cc")            # int or None (unlearned)
knob_mode = _cfg.get("knob_mode", "abs")  # "abs" | "rel2" (2's-complement relative)
forward_notes = bool(_cfg.get("forward_notes", True))
default_channel = int(_cfg.get("default_channel", 1))
ctrl_note_lo = int(_cfg.get("ctrl_note_lo", 60))   # the two Cs of the handshake /
ctrl_note_hi = int(_cfg.get("ctrl_note_hi", 72))   # chord-hold (note numbers)
_ctl = KeyboardControl(
    lo=ctrl_note_lo, hi=ctrl_note_hi,
    sticky_channel=_cfg.get("sticky_channel"),
    default_channel=default_channel,
)

_in_port = None
_in_name = ""
_out_port = None
_learn_armed = False
_learn_armed_at = 0.0
LEARN_TIMEOUT_S = 20.0   # a stale armed learn must never silently grab a
                         # surprise CC minutes later (e.g. the SMK's CC7
                         # volume blip on BLE reconnect)
_last_knob_val = None
_last_seen = 0.0
_lock = threading.Lock()
_fwd_ok = 0
_fwd_fail = 0
_last_fwd_err = ""
_dropped_bad = 0
_held = {}        # target channel (1-16) -> set of held notes we forwarded
_last_target_ch = None

_FORWARD_TYPES = {
    "note_on", "note_off", "pitchwheel", "aftertouch", "polytouch", "program_change",
}


def _save_cfg():
    try:
        save_section("knobctl", {
            "input_hints": input_hints,
            "output_hints": output_hints,
            "knob_cc": knob_cc,
            "knob_mode": knob_mode,
            "forward_notes": forward_notes,
            "default_channel": default_channel,
            "ctrl_note_lo": ctrl_note_lo,
            "ctrl_note_hi": ctrl_note_hi,
            "sticky_channel": _ctl.sticky_channel,
        })
    except Exception:
        pass


def _source():
    """'BT' | 'USB' | None — how the keyboard's input reaches us."""
    if _in_port is None:
        return None
    n = _in_name.lower()
    return "BT" if ("through" in n or "smk25mini" in n) else "USB"


def indicator():
    """Footer cell, right end of the timer row: (text, reverse)."""
    return _ctl.indicator(time.time(), _source())


def in_control_mode():
    return _ctl.in_control


def _user_activity():
    """A mode change or page step counts as a keypress for screensaver/pagecycle."""
    try:
        import midicrt as _m
        _m.wake_screensaver()
        pc = _m._pagecycle_module()
        if pc is not None:
            pc.notify_keypress()
    except Exception:
        pass


def _release_all():
    """Send note-offs for everything we forwarded and still consider held."""
    global _held
    if _out_port is None:
        _held = {}
        return
    for ch, notes in list(_held.items()):
        for n in list(notes):
            try:
                _out_port.send(mido.Message("note_off", note=n, velocity=0, channel=ch - 1))
            except Exception:
                pass
    _held = {}


def _apply_verdict(verdict):
    """Act on a KeyboardControl verdict. Returns True if the message is consumed."""
    if verdict == "forward":
        return False
    if verdict == "enter":
        _release_all()
        _user_activity()
    elif verdict in ("prev", "next"):
        try:
            import midicrt as _m
            _m.switch_page_relative(-1 if verdict == "prev" else 1)
        except Exception:
            pass
        _user_activity()
    return True


def arm_learn():
    global _learn_armed, _learn_armed_at
    with _lock:
        _learn_armed = True
        _learn_armed_at = time.time()


def knob_status():
    if _in_port is None:
        return "kbd offline"
    if _learn_armed:
        return "LEARNING..."
    fwd = f" fwd:{_fwd_ok}/{_fwd_fail}" + (f" [{_last_fwd_err}]" if _last_fwd_err else "")
    if knob_cc is None:
        return f"{_in_name.split(':')[0]} (no knob learned)" + fwd
    return f"cc{knob_cc} on {_in_name.split(':')[0]}" + fwd


def _open_input():
    global _in_port, _in_name
    try:
        names = list(mido.get_input_names())
    except Exception:
        return False
    for hint in input_hints:
        hl = str(hint).lower()
        for name in names:
            if hl in name.lower():
                try:
                    _in_port = mido.open_input(name)
                    _in_name = name
                    return True
                except Exception:
                    continue
    return False


def _ensure_out():
    global _out_port
    if _out_port is not None:
        return True
    try:
        names = list(mido.get_output_names())
    except Exception:
        return False
    for hint in output_hints:
        hl = str(hint).lower()
        for name in names:
            if hl in name.lower():
                try:
                    _out_port = mido.open_output(name)
                    return True
                except Exception:
                    continue
    return False


def _current_page():
    try:
        import midicrt
        return midicrt.PAGES.get(midicrt.current_page)
    except Exception:
        return None


def _knob_delta(value):
    """Convert a CC value into a signed step delta."""
    global _last_knob_val
    if knob_mode == "rel2":
        return value - 128 if value > 64 else value
    prev = _last_knob_val
    _last_knob_val = value
    if prev is None:
        return 0
    return value - prev


def _handle(msg):
    global _learn_armed, knob_cc, _last_knob_val
    now = time.time()
    if msg.type == "note_on" and msg.velocity > 0:
        if _apply_verdict(_ctl.note_on(msg.note, now)):
            return
    elif msg.type in ("note_off", "note_on"):
        if _apply_verdict(_ctl.note_off(msg.note, now)):
            return
    elif msg.type == "control_change" and knob_cc is not None and msg.control == knob_cc:
        _ctl.knob(msg.value, now)
    else:
        _ctl.touch(now)
        if _ctl.in_control:
            return   # control mode: only the knob gets through
    if msg.type == "control_change":
        with _lock:
            if _learn_armed and time.time() - _learn_armed_at > LEARN_TIMEOUT_S:
                _learn_armed = False   # expired un-consumed; ignore
            elif _learn_armed:
                _learn_armed = False
                knob_cc = msg.control
                _last_knob_val = None
                _save_cfg()
                return
        if knob_cc is not None and msg.control == knob_cc:
            try:
                import midicrt as _m
                _m.wake_screensaver()   # knob activity counts as user activity
            except Exception:
                pass
            page = _current_page()
            # absolute-position contract preferred: CC 0..127 = full range of
            # the focused field (right for pot-style knobs like the SMK-25)
            fn_abs = getattr(page, "on_knob_value", None)
            if fn_abs is not None and knob_mode != "rel2":
                try:
                    fn_abs(msg.value)
                except Exception:
                    pass
                return
            delta = _knob_delta(msg.value)
            if delta:
                fn = getattr(page, "on_knob_delta", None)
                if fn:
                    try:
                        fn(delta)
                    except Exception:
                        pass
            return
        # fall through: unbound CCs are forwarded like notes (mod wheel etc.)
    if not forward_notes:
        return
    if msg.type not in _FORWARD_TYPES and msg.type != "control_change":
        return
    global _fwd_ok, _fwd_fail, _last_fwd_err, _dropped_bad, _last_target_ch
    if not _ensure_out():
        _fwd_fail += 1
        _last_fwd_err = "no out port"
        return
    page = _current_page()
    prev_sticky = _ctl.sticky_channel
    _ctl.observe_page_channel(getattr(page, "note_target_channel", None))
    if _ctl.sticky_channel != prev_sticky:
        _save_cfg()
    ch = _ctl.target_channel()
    try:
        # Page (target channel) changed while notes are held: release them on
        # the old channel first, otherwise their note-offs land elsewhere.
        if _last_target_ch is not None and ch != _last_target_ch:
            for n in list(_held.get(_last_target_ch, ())):
                _out_port.send(mido.Message("note_off", note=n, velocity=0, channel=_last_target_ch - 1))
            _held.pop(_last_target_ch, None)
        _last_target_ch = ch
        if hasattr(msg, "channel"):
            msg = msg.copy(channel=ch - 1)
        if msg.type == "note_on" and msg.velocity > 0:
            _held.setdefault(ch, set()).add(msg.note)
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            _held.get(ch, set()).discard(msg.note)
        _out_port.send(msg)
        _fwd_ok += 1
    except Exception as exc:
        _fwd_fail += 1
        _last_fwd_err = str(exc)[:40]


# If the BLE keyboard drops and reconnects, bluetoothd recreates its seq
# client and our subscription dies SILENTLY — reads keep succeeding with no
# data, no exception.  So: if nothing has arrived for IDLE_REOPEN_SECS,
# quietly close and reopen (rescanning hints fresh, which also upgrades us
# from the Midi Through fallback back to the real SMK port when it returns).
IDLE_REOPEN_SECS = 20.0


def _worker():
    global _in_port, _in_name, _last_seen
    last_reopen = time.time()
    while True:
        if _in_port is None:
            if not _open_input():
                time.sleep(3.0)
                continue
            last_reopen = time.time()
        try:
            got = False
            for msg in _in_port.iter_pending():
                got = True
                _last_seen = time.time()
                _handle(msg)
            now = time.time()
            if _ctl.tick(now):          # chord-hold ended control mode
                _release_all()
                _user_activity()
            if not got and now - max(_last_seen, last_reopen) > IDLE_REOPEN_SECS:
                try:
                    _in_port.close()
                except Exception:
                    pass
                _in_port = None
                _in_name = ""
                continue
            time.sleep(0.005 if got else 0.02)
        except Exception:
            # BLE dropped or backend hiccup: close and rescan
            try:
                _in_port.close()
            except Exception:
                pass
            _in_port = None
            _in_name = ""
            time.sleep(2.0)


_thread = threading.Thread(target=_worker, name="knobctl", daemon=True)
_thread.start()
