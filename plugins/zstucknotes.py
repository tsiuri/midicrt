# -*- coding: utf-8 -*-
# Plugin: Stuck Note Monitor
#
# Tracks per-channel note-on/off state and warns when a note stays on
# beyond a threshold (i.e., likely stuck). Sustain pedal can optionally
# suppress warnings while held.

import time
import sys
import os
import midicrt
import mido
from configutil import load_section, save_section

# --- Config ---
WARN_AFTER = 2.0    # seconds before a note is considered "stuck"
CRIT_AFTER = 10.0   # seconds before a note is considered "critical"
MAX_LIST = 3        # number of stuck notes to list
Y_POS_OFFSET = 4    # draw 4 lines from bottom (avoids other plugin rows)
HOLD_AFTER = 15.0   # seconds to keep message after all stuck notes clear
SUSPEND_WHEN_SUSTAIN = True  # suppress warnings while sustain is held

PANIC_ON_CRIT = True
PANIC_SCOPE = "channel"  # "channel" or "all"
PANIC_OUTPUT_NAME = "GreenCRT Panic"
PANIC_COOLDOWN = 3.0  # seconds between panic sends per channel
PANIC_AUTOCONNECT = True
PANIC_DST_HINTS = ["USB MIDI Interface", "USB MIDI", "MIDI 1"]

_cfg = load_section("stuck_notes")
if _cfg is None:
    _cfg = {}
try:
    WARN_AFTER = float(_cfg.get("warn_after", WARN_AFTER))
    CRIT_AFTER = float(_cfg.get("crit_after", CRIT_AFTER))
    HOLD_AFTER = float(_cfg.get("hold_after", HOLD_AFTER))
    MAX_LIST = int(_cfg.get("max_list", MAX_LIST))
    Y_POS_OFFSET = int(_cfg.get("y_pos_offset", Y_POS_OFFSET))
    SUSPEND_WHEN_SUSTAIN = bool(_cfg.get("suspend_when_sustain", SUSPEND_WHEN_SUSTAIN))
    PANIC_ON_CRIT = bool(_cfg.get("panic_on_crit", PANIC_ON_CRIT))
    PANIC_SCOPE = str(_cfg.get("panic_scope", PANIC_SCOPE))
    PANIC_OUTPUT_NAME = str(_cfg.get("panic_output_name", PANIC_OUTPUT_NAME))
    PANIC_COOLDOWN = float(_cfg.get("panic_cooldown", PANIC_COOLDOWN))
    PANIC_AUTOCONNECT = bool(_cfg.get("panic_autoconnect", PANIC_AUTOCONNECT))
    hints = _cfg.get("panic_dst_hints", PANIC_DST_HINTS)
    if isinstance(hints, str):
        PANIC_DST_HINTS = [h.strip() for h in hints.split(",") if h.strip()]
    elif isinstance(hints, list):
        PANIC_DST_HINTS = [str(h).strip() for h in hints if str(h).strip()]
except Exception:
    pass

try:
    save_section("stuck_notes", {
        "warn_after": float(WARN_AFTER),
        "crit_after": float(CRIT_AFTER),
        "hold_after": float(HOLD_AFTER),
        "max_list": int(MAX_LIST),
        "y_pos_offset": int(Y_POS_OFFSET),
        "suspend_when_sustain": bool(SUSPEND_WHEN_SUSTAIN),
        "panic_on_crit": bool(PANIC_ON_CRIT),
        "panic_scope": str(PANIC_SCOPE),
        "panic_output_name": str(PANIC_OUTPUT_NAME),
        "panic_cooldown": float(PANIC_COOLDOWN),
        "panic_autoconnect": bool(PANIC_AUTOCONNECT),
        "panic_dst_hints": list(PANIC_DST_HINTS),
    })
except Exception:
    pass

LOG_PATH = os.path.join(os.path.dirname(midicrt.__file__), "log.txt")

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# (ch, note) -> {"count": int, "last_on": float}
_active = {}
# ch -> sustain state
_sustain = {ch: False for ch in range(1, 17)}

_ss_cached = None
_out_port = None
_panic_autoconnect_done = False
_levels = {}  # (ch, note) -> "none" | "warn" | "crit"
_last_message = ""
_last_message_time = 0.0
_last_message_level = ""
_had_stuck = False
_last_stuck_snapshot = []
_panic_last = {ch: 0.0 for ch in range(1, 17)}
_stuck_counts_note = {}  # note -> count of stuck events
_stuck_counts_pc = {pc: 0 for pc in range(12)}
_stuck_recent = []  # list of (ts, ch, note, level)


def _screensaver_active():
    global _ss_cached
    if _ss_cached is None:
        _ss_cached = next(
            (m for m in midicrt.PLUGINS if hasattr(m, "is_active") and hasattr(m, "deactivate")),
            None,
        )
    if _ss_cached and hasattr(_ss_cached, "is_active"):
        try:
            return _ss_cached.is_active()
        except Exception:
            return False
    return False


def _note_on(ch, note):
    now = time.time()
    key = (ch, note)
    entry = _active.get(key)
    if entry:
        entry["count"] += 1
        entry["last_on"] = now
    else:
        _active[key] = {"count": 1, "last_on": now}


def _note_off(ch, note):
    key = (ch, note)
    entry = _active.get(key)
    if not entry:
        return
    entry["count"] -= 1
    if entry["count"] <= 0:
        prev_level = _levels.get(key, "none")
        if prev_level in ("warn", "crit"):
            age = time.time() - entry["last_on"]
            _log(f"CLEARED ch={ch:02d} note={_fmt_note(note)} age={age:0.1f}s")
        _active.pop(key, None)
        # clear level state on full release
        _levels.pop(key, None)


def _clear_channel(ch):
    for key in [k for k in _active.keys() if k[0] == ch]:
        prev_level = _levels.get(key, "none")
        if prev_level in ("warn", "crit"):
            note = key[1]
            age = time.time() - _active[key]["last_on"]
            _log(f"CLEARED ch={ch:02d} note={_fmt_note(note)} age={age:0.1f}s (via CC)")
        _active.pop(key, None)
        _levels.pop(key, None)


def _fmt_note(note_num):
    name = NOTE_NAMES[note_num % 12]
    octave = (note_num // 12) - 1 + 2  # match polydisplay octave shift
    return f"{name}{octave}({note_num:03d})"


def handle(msg):
    if msg.type == "note_on":
        ch = msg.channel + 1
        if msg.velocity == 0:
            _note_off(ch, msg.note)
        else:
            _note_on(ch, msg.note)
    elif msg.type == "note_off":
        ch = msg.channel + 1
        _note_off(ch, msg.note)
    elif msg.type == "control_change":
        ch = msg.channel + 1
        if msg.control == 64:
            _sustain[ch] = msg.value >= 64
        elif msg.control in (120, 123):
            _clear_channel(ch)
        elif msg.control == 121:
            _sustain[ch] = False


def draw(state=None):
    global _last_message, _last_message_time, _last_message_level
    global _had_stuck, _last_stuck_snapshot
    if _screensaver_active():
        return
    if PANIC_ON_CRIT and _out_port is None:
        _ensure_out()

    now = time.time()
    stuck = []
    for (ch, note), entry in list(_active.items()):
        if entry["count"] <= 0:
            continue
        if SUSPEND_WHEN_SUSTAIN and _sustain.get(ch, False):
            _levels[(ch, note)] = "none"
            continue
        age = now - entry["last_on"]
        level = "crit" if age >= CRIT_AFTER else ("warn" if age >= WARN_AFTER else "none")
        prev = _levels.get((ch, note), "none")
        if level != prev:
            _levels[(ch, note)] = level
            if level == "warn":
                _log(f"WARN ch={ch:02d} note={_fmt_note(note)} age={age:0.1f}s")
            elif level == "crit":
                _log(f"CRIT ch={ch:02d} note={_fmt_note(note)} age={age:0.1f}s")
                if PANIC_ON_CRIT:
                    last_sent = _panic_last.get(ch, 0.0)
                    if (now - last_sent) >= PANIC_COOLDOWN:
                        if _send_all_notes_off(channel=ch):
                            _panic_last[ch] = now
                            _log(f"PANIC all-notes-off sent (scope={PANIC_SCOPE}, ch={ch:02d})")
            if level in ("warn", "crit") and prev == "none":
                _stuck_counts_note[note] = _stuck_counts_note.get(note, 0) + 1
                _stuck_counts_pc[note % 12] = _stuck_counts_pc.get(note % 12, 0) + 1
                _stuck_recent.append((now, ch, note, level))
                if len(_stuck_recent) > 10:
                    _stuck_recent.pop(0)
        if level in ("warn", "crit"):
            stuck.append((age, ch, note))

    if not stuck:
        # if we just cleared, keep last message up for HOLD_AFTER seconds
        if _had_stuck:
            _had_stuck = False
            if _last_stuck_snapshot:
                parts = [f"CH{ch:02d} {_fmt_note(note)} {age:4.1f}s" for age, ch, note in _last_stuck_snapshot[:MAX_LIST]]
                extra = len(_last_stuck_snapshot) - MAX_LIST
                if extra > 0:
                    parts.append(f"+{extra} more")
                _last_message = "STUCK CLEARED: " + " | ".join(parts)
            else:
                _last_message = "STUCK CLEARED"
            _last_message_time = now
            _last_message_level = "clear"
            _log("CLEARED all stuck notes")

        if _last_message and (now - _last_message_time) <= HOLD_AFTER:
            y = max(0, midicrt.SCREEN_ROWS - Y_POS_OFFSET)
            midicrt.draw_line(y, _last_message)
        else:
            y = max(0, midicrt.SCREEN_ROWS - Y_POS_OFFSET)
            midicrt.draw_line(y, "")
        return

    stuck.sort(reverse=True)
    worst_age, _, _ = stuck[0]
    level = "CRIT" if worst_age >= CRIT_AFTER else "WARN"

    parts = []
    for age, ch, note in stuck[:MAX_LIST]:
        parts.append(f"CH{ch:02d} {_fmt_note(note)} {age:4.1f}s")

    extra = len(stuck) - MAX_LIST
    if extra > 0:
        parts.append(f"+{extra} more")

    text = f"STUCK {level}: " + " | ".join(parts)

    y = max(0, midicrt.SCREEN_ROWS - Y_POS_OFFSET)
    midicrt.draw_line(y, text)
    _last_message = text
    _last_message_time = now
    _last_message_level = level
    _had_stuck = True
    _last_stuck_snapshot = stuck[:]

    # emphasize critical with reverse video
    if level == "CRIT":
        t = midicrt.term
        sys.stdout.write(t.move_yx(y, 0) + t.reverse(text[:midicrt.SCREEN_COLS]) + t.normal)
        sys.stdout.flush()
def _log(msg):
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[StuckNotes] {ts} {msg}\n")
    except Exception:
        pass


def _ensure_out():
    global _out_port
    if _out_port is not None:
        return
    existing = getattr(midicrt, "PANIC_OUT_PORT", None)
    if existing is not None:
        _out_port = existing
        return
    try:
        try:
            _out_port = mido.open_output(PANIC_OUTPUT_NAME)
        except (IOError, OSError):
            _out_port = mido.open_output(PANIC_OUTPUT_NAME, virtual=True)
    except Exception:
        _out_port = None
    if _out_port is not None:
        _autoconnect_panic()


def get_stuck_stats():
    return {
        "note_counts": dict(_stuck_counts_note),
        "pc_counts": dict(_stuck_counts_pc),
        "recent": list(_stuck_recent),
        "active": list(_active.keys()),
    }


def _find_port(entries, hints):
    for hint in hints:
        h = hint.lower().strip()
        if not h:
            continue
        for client_id, client_name, port_id, port_name in entries:
            text = f"{client_name} {port_name}".lower()
            if h in text:
                return f"{client_id}:{port_id}", client_name, port_name
    return None, None, None


def _autoconnect_panic():
    global _panic_autoconnect_done
    if not PANIC_AUTOCONNECT or _panic_autoconnect_done:
        return
    try:
        import subprocess
    except Exception:
        return

    src_hints = [PANIC_OUTPUT_NAME]
    dst_hints = PANIC_DST_HINTS
    for _ in range(5):
        try:
            outs = midicrt._parse_aconnect("-o")
            ins = midicrt._parse_aconnect("-i")
            src_id, _, _ = _find_port(outs, src_hints)
            dst_id, _, _ = _find_port(ins, dst_hints)
            if src_id and dst_id:
                subprocess.run(["aconnect", src_id, dst_id], check=True)
                _panic_autoconnect_done = True
                _log(f"PANIC autoconnected {src_id} -> {dst_id}")
                return
        except Exception:
            pass
        time.sleep(0.2)


def _send_all_notes_off(channel=None):
    _ensure_out()
    if _out_port is None:
        return False
    try:
        if PANIC_SCOPE == "all" or channel is None:
            for ch in range(1, 17):
                _out_port.send(mido.Message("control_change", control=123, value=0, channel=ch - 1))
        else:
            _out_port.send(mido.Message("control_change", control=123, value=0, channel=channel - 1))
        return True
    except Exception:
        return False
