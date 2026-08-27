# -*- coding: utf-8 -*-
# Plugin: SysEx command receiver
#
# Listens for SysEx messages addressed to midicrt and dispatches commands.
# This allows remote control from the Cirklon or any device that can send SysEx.
#
# Message formats:
#   Legacy:    F0  7D  6D  63  <cmd>  [args...]  F7
#   Versioned: F0  7D  6D  63  <ver>  <cmd>  [args...]  F7
#
#   7D        = MIDI non-commercial / private-use manufacturer ID
#   6D 63     = ASCII 'mc' (midicrt identifier)
#   <ver>     = protocol byte (0x40 negotiate, 0x41.. => ver = byte - 0x40)
#   <cmd>     = command byte (see below)
#   [args...] = zero or more argument bytes (0–127)
#
# Commands:
#   01 <page>    — switch to any loaded page ID (current map: 0–15)
#   02 <0|1>     — screensaver: 0 = wake/deactivate, 1 = activate now
#   03 <0|1>     — page cycler: 0 = disable, 1 = enable
#   04 [bars]    — dump recent bars to MIDI capture file
#   10           — capability query (versioned frame only)

import sys
import time
import os
import mido
import midicrt

PREFIX = (0x7D, 0x6D, 0x63)  # non-commercial ID + 'mc'

# Legacy frame:    F0 7D 6D 63 <cmd> [args...] F7
# Versioned frame: F0 7D 6D 63 <ver> <cmd> [args...] F7
#   ver byte 0x40       = negotiation request (use highest supported)
#   ver byte 0x41..0x7F = protocol version N where N = byte - 0x40
VERSION_BASE = 0x40
NEGOTIATE_VERSION_BYTE = VERSION_BASE
SUPPORTED_PROTOCOL_VERSIONS = (1,)

CMD_SWITCH_PAGE = 0x01
CMD_SCREENSAVER = 0x02
CMD_PAGE_CYCLE = 0x03
CMD_CAPTURE_RECENT = 0x04
CMD_CAPABILITIES = 0x10

MSG_DISPLAY_SECS = 5.0  # how long to show each command in the footer
SYSEX_LOG_PATH = os.path.join(os.path.dirname(midicrt.__file__), "sysex.log")
SYSEX_SPLIT_DIR = os.path.join(os.path.dirname(midicrt.__file__), "sysex.d")

# Config (section "sysex"). The per-message split-file writer is DEBUG tooling
# that, left on, wrote one file per incoming sysex into an unbounded dir — with
# controller dumps flooding in, the directory grew to thousands of files and
# every new write stalled the scheduler (froze the GUI). So: split is OFF by
# default, and bounded to split_max_files (oldest pruned) when enabled. The
# single append-log stays on (cheap) but can be silenced too.
try:
    from configutil import load_section as _load_section
    _sxcfg = _load_section("sysex") or {}
except Exception:
    _sxcfg = {}
SYSEX_LOG_ALL = bool(_sxcfg.get("log_all", True))
SYSEX_SPLIT_ENABLED = bool(_sxcfg.get("split_enabled", False))
SYSEX_SPLIT_MAX_FILES = int(_sxcfg.get("split_max_files", 500))
_sysex_seq = 0
_tx_port = None


def _log(text):
    midicrt.sysex_status = text
    midicrt.sysex_status_time = time.time()

def _log_sysex(data, note="rx"):
    if not SYSEX_LOG_PATH:
        return
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        hex_bytes = " ".join(f"{b:02X}" for b in data)
        full = f"F0 {hex_bytes} F7" if hex_bytes else "F0 F7"
        with open(SYSEX_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[SysEx] {ts} {note} {full}\n")
    except Exception:
        pass

def _split_sysex(data, note="rx"):
    global _sysex_seq
    if not SYSEX_SPLIT_ENABLED or not SYSEX_SPLIT_DIR:
        return
    try:
        os.makedirs(SYSEX_SPLIT_DIR, exist_ok=True)
        _sysex_seq += 1
        ts = time.strftime("%Y%m%d-%H%M%S")
        hex_bytes = " ".join(f"{b:02X}" for b in data)
        full = f"F0 {hex_bytes} F7" if hex_bytes else "F0 F7"
        fname = f"{ts}-{_sysex_seq:06d}-{note}.syx"
        path = os.path.join(SYSEX_SPLIT_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(full + "\n")
        # bound the directory: prune oldest beyond the cap (checked cheaply,
        # only every 64 writes, to avoid listing a big dir every message)
        if SYSEX_SPLIT_MAX_FILES > 0 and (_sysex_seq & 0x3F) == 0:
            files = sorted(f for f in os.listdir(SYSEX_SPLIT_DIR) if f.endswith(".syx"))
            excess = len(files) - SYSEX_SPLIT_MAX_FILES
            for old_f in files[:max(0, excess)]:
                try:
                    os.remove(os.path.join(SYSEX_SPLIT_DIR, old_f))
                except Exception:
                    pass
    except Exception:
        pass

def _find(attr):
    return next((m for m in midicrt.PLUGINS if hasattr(m, attr)), None)


def _format_status(version, cmd, status, detail=""):
    vtxt = "legacy" if version is None else f"v{version}"
    base = f"sx:{vtxt} cmd=0x{cmd:02X} {status}"
    return f"{base} {detail}".strip()


def _send_reply(version, cmd, status, payload=()):
    global _tx_port
    frame = PREFIX + (VERSION_BASE + int(version), cmd, status) + tuple(payload)
    try:
        if _tx_port is None:
            names = mido.get_output_names()
            target = next(
                (
                    name
                    for name in names
                    if "greencrt monitor" in name.lower() or "rtmidiin client" in name.lower()
                ),
                None,
            )
            if target is None:
                _log(_format_status(version, cmd, "warn", "reply-no-port"))
                return False
            _tx_port = mido.open_output(target)
        _tx_port.send(mido.Message("sysex", data=frame))
        _log_sysex(frame, "tx")
        _split_sysex(frame, "tx")
        return True
    except Exception as exc:
        _tx_port = None
        _log(_format_status(version, cmd, "warn", f"reply-fail {exc}"))
        return False


def _capabilities_payload(version):
    pages = sorted(midicrt.PAGES.keys())
    profile_map = {"run_tui": 1, "run_pixel": 2}
    backend_map = {"blessed": 1, "pixel": 2}
    feature_flags = [
        int(hasattr(midicrt, "trigger_capture_recent")),  # capture
        int(_find("is_active") is not None),              # screensaver
        int(_find("notify_keypress") is not None),        # page cycler
    ]
    payload = [
        version,
        profile_map.get(getattr(midicrt, "ACTIVE_PROFILE", ""), 0),
        backend_map.get(getattr(midicrt, "ACTIVE_RENDER_BACKEND", ""), 0),
        len(SUPPORTED_PROTOCOL_VERSIONS),
        *SUPPORTED_PROTOCOL_VERSIONS,
        len(feature_flags),
        *feature_flags,
        len(pages),
        *pages,
    ]
    return tuple(max(0, min(127, int(v))) for v in payload)


def handle(msg):
    if msg.type != "sysex":
        return

    data = tuple(msg.data)
    if SYSEX_LOG_ALL:
        _log_sysex(data, "rx")
        _split_sysex(data, "rx")
    n = len(PREFIX)

    if len(data) < n + 1 or data[:n] != PREFIX:
        return

    # wake screensaver on any valid midicrt SysEx command
    ss = _find("is_active")
    if ss and ss.is_active():
        ss.deactivate()

    marker = data[n]
    if marker >= VERSION_BASE:
        if len(data) < n + 2:
            _log(_format_status(0, 0x00, "err", "missing-cmd"))
            return
        if marker == NEGOTIATE_VERSION_BYTE:
            version = max(SUPPORTED_PROTOCOL_VERSIONS)
        else:
            version = marker - VERSION_BASE
            if version not in SUPPORTED_PROTOCOL_VERSIONS:
                cmd = data[n + 1]
                _log(_format_status(version, cmd, "err", "unsupported-version"))
                _send_reply(max(SUPPORTED_PROTOCOL_VERSIONS), cmd, 0x01, SUPPORTED_PROTOCOL_VERSIONS)
                return
        cmd = data[n + 1]
        args = data[n + 2:]
        _dispatch(cmd, args, version)
        return

    cmd = marker
    args = data[n + 1:]
    _dispatch(cmd, args, None)


def _dispatch(cmd, args, version):
    # --- 01: switch page ---
    if cmd == CMD_SWITCH_PAGE:
        if not args:
            _log(_format_status(version, cmd, "err", "missing-page"))
            return
        page = args[0]
        ok, resolved = midicrt.switch_page(page)
        if not ok:
            _log(_format_status(version, cmd, "err", f"invalid-page {resolved}"))
            if version is not None:
                _send_reply(version, cmd, 0x01, (resolved,))
            return
        _log(_format_status(version, cmd, "ok", f"page->{resolved}"))
        if version is not None:
            _send_reply(version, cmd, 0x00, (resolved,))

    # --- 02: screensaver control ---
    elif cmd == CMD_SCREENSAVER:
        if not args:
            _log(_format_status(version, cmd, "err", "missing-arg"))
            return
        ss = _find("is_active")
        if not ss:
            _log(_format_status(version, cmd, "err", "no-screensaver"))
            return
        if args[0] == 0:
            ss.deactivate()
            _log(_format_status(version, cmd, "ok", "screen-on"))
        else:
            ss._last_activity = time.time() - ss.IDLE_TIMEOUT - 1
            _log(_format_status(version, cmd, "ok", "screen-off"))
        if version is not None:
            _send_reply(version, cmd, 0x00, (int(args[0] != 0),))

    # --- 03: page cycler control ---
    elif cmd == CMD_PAGE_CYCLE:
        if not args:
            _log(_format_status(version, cmd, "err", "missing-arg"))
            return
        pc = _find("notify_keypress")
        if not pc:
            _log(_format_status(version, cmd, "err", "no-cycler"))
            return
        pc.ENABLED = bool(args[0])
        _log(_format_status(version, cmd, "ok", f"cycle-{'on' if args[0] else 'off'}"))
        if version is not None:
            _send_reply(version, cmd, 0x00, (int(bool(args[0])),))

    # --- 04: capture recent bars ---
    elif cmd == CMD_CAPTURE_RECENT:
        bars = None
        if args:
            try:
                bars = max(1, int(args[0]))
            except Exception:
                bars = None
        try:
            ok, message, _ = midicrt.trigger_capture_recent(trigger="sysex:04", bars=bars)
            if ok:
                _log(_format_status(version, cmd, "ok", f"bars={bars or 0}"))
                if version is not None:
                    _send_reply(version, cmd, 0x00, (bars or 0,))
            else:
                _log(_format_status(version, cmd, "err", "capture-fail"))
                if version is not None:
                    _send_reply(version, cmd, 0x01)
        except Exception as exc:
            _log(_format_status(version, cmd, "err", f"capture-exc {exc}"))
            if version is not None:
                _send_reply(version, cmd, 0x01)

    # --- 10: capabilities query ---
    elif cmd == CMD_CAPABILITIES:
        if version is None:
            _log(_format_status(version, cmd, "err", "requires-versioned-frame"))
            return
        payload = _capabilities_payload(version)
        sent = _send_reply(version, cmd, 0x00, payload)
        _log(_format_status(version, cmd, "ok", "caps-sent" if sent else "caps-local-only"))

    else:
        _log(_format_status(version, cmd, "err", "unknown-cmd"))
        if version is not None:
            _send_reply(version, cmd, 0x01)
