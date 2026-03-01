#!/usr/bin/env python3
"""Small CLI for sending MIDI messages via ALSA `aseqsend`.

Examples:
  ./midisend list
  ./midisend note C4 --vel 96 --dur-ms 120
  ./midisend cc 1 64 --ch 2
  ./midisend sysex 7D 6D 63 40 10
  ./midisend send-scale C4 --vel-start 16 --vel-end 127
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass


NOTE_BASE = {
    "C": 0,
    "D": 2,
    "E": 4,
    "F": 5,
    "G": 7,
    "A": 9,
    "B": 11,
}

SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11, 12],
    "minor": [0, 2, 3, 5, 7, 8, 10, 12],
    "chromatic": list(range(13)),
}


@dataclass
class PortInfo:
    client_id: int
    client_name: str
    port_id: int
    port_name: str

    @property
    def addr(self) -> str:
        return f"{self.client_id}:{self.port_id}"


def _require_tools() -> None:
    if shutil.which("aseqsend") is None:
        raise RuntimeError("`aseqsend` not found. Install `alsa-utils`.")
    if shutil.which("aconnect") is None:
        raise RuntimeError("`aconnect` not found. Install `alsa-utils`.")


def _run(cmd: list[str], *, capture: bool = False) -> str:
    proc = subprocess.run(
        cmd,
        check=True,
        text=True,
        capture_output=capture,
    )
    return proc.stdout if capture else ""


def _parse_ports() -> list[PortInfo]:
    out = _run(["aconnect", "-l"], capture=True)
    ports: list[PortInfo] = []
    cur_client_id: int | None = None
    cur_client_name: str | None = None

    client_re = re.compile(r"^client\s+(\d+):\s+'([^']+)'")
    port_re = re.compile(r"^\s+(\d+)\s+'([^']+)'")

    for line in out.splitlines():
        m_client = client_re.match(line)
        if m_client:
            cur_client_id = int(m_client.group(1))
            cur_client_name = m_client.group(2)
            continue

        m_port = port_re.match(line)
        if m_port and cur_client_id is not None and cur_client_name is not None:
            ports.append(
                PortInfo(
                    client_id=cur_client_id,
                    client_name=cur_client_name,
                    port_id=int(m_port.group(1)),
                    port_name=m_port.group(2),
                )
            )

    return ports


def _default_port(ports: list[PortInfo]) -> str | None:
    env_port = os.environ.get("MIDISEND_PORT", "").strip()
    if env_port:
        return env_port

    best: tuple[int, str] | None = None
    for p in ports:
        c = p.client_name.lower()
        n = p.port_name.lower()
        if c in {"system", "midi through"}:
            continue
        score = 0
        if "rtmidiin client" in c:
            score += 200
        if "rtmidi input" in n:
            score += 40
        if "greencrt monitor" in n:
            score += 120
        if "greencrt" in c:
            score += 80
        if "usb midi interface" in c:
            score += 20
        if score > 0:
            cand = (score, p.addr)
            if best is None or cand[0] > best[0]:
                best = cand

    if best is not None:
        return best[1]

    for p in ports:
        c = p.client_name.lower()
        if c not in {"system", "midi through"}:
            return p.addr
    return None


def _parse_note(note: str) -> int:
    s = note.strip()
    if s.isdigit():
        v = int(s)
        if 0 <= v <= 127:
            return v
        raise ValueError(f"note out of range: {v}")

    m = re.fullmatch(r"([A-Ga-g])([#b]?)(-?\d+)", s)
    if not m:
        raise ValueError(f"invalid note: {note} (expected e.g. C4, F#3, Bb2, or 0..127)")

    letter = m.group(1).upper()
    accidental = m.group(2)
    octave = int(m.group(3))

    semitone = NOTE_BASE[letter]
    if accidental == "#":
        semitone += 1
    elif accidental == "b":
        semitone -= 1
    semitone %= 12

    midi = (octave + 1) * 12 + semitone
    if not (0 <= midi <= 127):
        raise ValueError(f"note out of range: {note} -> {midi}")
    return midi


def _hex_byte(v: int) -> str:
    if not (0 <= v <= 255):
        raise ValueError(f"byte out of range: {v}")
    return f"{v:02X}"


def _parse_hex_tokens(tokens: list[str]) -> list[int]:
    out: list[int] = []
    for tok in tokens:
        t = tok.strip().replace(",", " ")
        if not t:
            continue
        for part in t.split():
            p = part.upper()
            if p.startswith("0X"):
                p = p[2:]
            if not p:
                continue
            out.append(int(p, 16))
    return out


def _status(base: int, ch_1_16: int) -> int:
    ch = int(ch_1_16)
    if ch < 1 or ch > 16:
        raise ValueError("channel must be 1..16")
    return base + (ch - 1)


def _send_bytes(port: str, data: list[int], *, dry_run: bool = False) -> None:
    msg = " ".join(_hex_byte(v) for v in data)
    cmd = ["aseqsend", "-p", port, msg]
    if dry_run:
        print("DRY:", " ".join(cmd))
        return
    _run(cmd, capture=False)


def _cmd_list(_args: argparse.Namespace) -> int:
    ports = _parse_ports()
    default = _default_port(ports)
    print("ALSA sequencer ports:")
    for p in ports:
        mark = "*" if p.addr == default else " "
        print(f"{mark} {p.addr:<6}  {p.client_name}  ::  {p.port_name}")
    if default:
        print(f"\nDefault send port: {default} (override with --port or MIDISEND_PORT)")
    else:
        print("\nNo default port found; use --port CLIENT:PORT")
    return 0


def _resolve_port(args: argparse.Namespace) -> str:
    if getattr(args, "port", None):
        return str(args.port)
    ports = _parse_ports()
    default = _default_port(ports)
    if not default:
        raise RuntimeError("no default port found (use --port CLIENT:PORT)")
    return default


def _cmd_note(args: argparse.Namespace) -> int:
    port = _resolve_port(args)
    note_num = _parse_note(args.note)
    vel = int(args.vel)
    if vel < 1 or vel > 127:
        raise ValueError("velocity must be 1..127")

    on = [_status(0x90, args.ch), note_num, vel]
    off = [_status(0x80, args.ch), note_num, int(args.off_vel)]
    _send_bytes(port, on, dry_run=args.dry_run)
    if args.dur_ms > 0:
        time.sleep(float(args.dur_ms) / 1000.0)
        _send_bytes(port, off, dry_run=args.dry_run)
    return 0


def _cmd_cc(args: argparse.Namespace) -> int:
    port = _resolve_port(args)
    cc = int(args.cc)
    val = int(args.value)
    if cc < 0 or cc > 127:
        raise ValueError("cc must be 0..127")
    if val < 0 or val > 127:
        raise ValueError("value must be 0..127")
    msg = [_status(0xB0, args.ch), cc, val]
    _send_bytes(port, msg, dry_run=args.dry_run)
    return 0


def _cmd_pc(args: argparse.Namespace) -> int:
    port = _resolve_port(args)
    program = int(args.program)
    if program < 0 or program > 127:
        raise ValueError("program must be 0..127")
    msg = [_status(0xC0, args.ch), program]
    _send_bytes(port, msg, dry_run=args.dry_run)
    return 0


def _cmd_raw(args: argparse.Namespace) -> int:
    port = _resolve_port(args)
    data = _parse_hex_tokens(args.bytes)
    if not data:
        raise ValueError("raw requires at least one byte")
    _send_bytes(port, data, dry_run=args.dry_run)
    return 0


def _cmd_sysex(args: argparse.Namespace) -> int:
    port = _resolve_port(args)
    data = _parse_hex_tokens(args.bytes)
    if not data:
        raise ValueError("sysex requires at least one byte")
    if data[0] != 0xF0:
        data = [0xF0] + data
    if data[-1] != 0xF7:
        data = data + [0xF7]
    _send_bytes(port, data, dry_run=args.dry_run)
    return 0


def _cmd_scale(args: argparse.Namespace) -> int:
    port = _resolve_port(args)
    root = _parse_note(args.root)
    pattern = SCALES[args.scale_type]
    octaves = max(1, int(args.octaves))

    notes: list[int] = []
    for oc in range(octaves):
        for off in pattern[:-1]:
            n = root + off + (12 * oc)
            if 0 <= n <= 127:
                notes.append(n)
    top = root + 12 * octaves
    if 0 <= top <= 127:
        notes.append(top)
    if not notes:
        raise ValueError("scale produced no in-range notes")

    v0 = int(args.vel_start)
    v1 = int(args.vel_end)
    if not (1 <= v0 <= 127 and 1 <= v1 <= 127):
        raise ValueError("vel-start and vel-end must be 1..127")

    count = len(notes)
    gate_s = max(0.0, float(args.gate_ms) / 1000.0)
    step_s = max(gate_s, float(args.step_ms) / 1000.0)
    sleep_after_off = max(0.0, step_s - gate_s)

    for i, note_num in enumerate(notes):
        if count <= 1:
            vel = v1
        else:
            vel = int(round(v0 + (v1 - v0) * (i / (count - 1))))
        on = [_status(0x90, args.ch), note_num, vel]
        off = [_status(0x80, args.ch), note_num, int(args.off_vel)]
        _send_bytes(port, on, dry_run=args.dry_run)
        if gate_s > 0:
            time.sleep(gate_s)
        _send_bytes(port, off, dry_run=args.dry_run)
        if sleep_after_off > 0:
            time.sleep(sleep_after_off)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="midisend", description="Send MIDI messages via ALSA aseqsend")
    p.add_argument("--port", help="destination ALSA sequencer port (e.g. 129:0)")
    p.add_argument("--dry-run", action="store_true", help="print commands instead of sending")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("list", help="list ALSA sequencer ports and chosen default")
    sp.set_defaults(func=_cmd_list)

    sp = sub.add_parser("note", help="send note on (+ optional timed note off)")
    sp.add_argument("note", help="note name (C4, F#3, Bb2) or MIDI number 0..127")
    sp.add_argument("--ch", type=int, default=1, help="MIDI channel 1..16 (default: 1)")
    sp.add_argument("--vel", type=int, default=100, help="velocity 1..127 (default: 100)")
    sp.add_argument("--dur-ms", type=int, default=120, help="note duration in ms; 0 sends note-on only")
    sp.add_argument("--off-vel", type=int, default=0, help="note-off velocity 0..127")
    sp.set_defaults(func=_cmd_note)

    sp = sub.add_parser("cc", help="send control change")
    sp.add_argument("cc", type=int, help="CC number 0..127")
    sp.add_argument("value", type=int, help="CC value 0..127")
    sp.add_argument("--ch", type=int, default=1, help="MIDI channel 1..16 (default: 1)")
    sp.set_defaults(func=_cmd_cc)

    sp = sub.add_parser("pc", help="send program change")
    sp.add_argument("program", type=int, help="program number 0..127")
    sp.add_argument("--ch", type=int, default=1, help="MIDI channel 1..16 (default: 1)")
    sp.set_defaults(func=_cmd_pc)

    sp = sub.add_parser("raw", help="send raw status/data bytes")
    sp.add_argument("bytes", nargs="+", help="hex bytes (e.g. 90 3C 64)")
    sp.set_defaults(func=_cmd_raw)

    sp = sub.add_parser("sysex", help="send sysex bytes (auto-wraps F0/F7 if missing)")
    sp.add_argument("bytes", nargs="+", help="hex payload bytes")
    sp.set_defaults(func=_cmd_sysex)

    sp = sub.add_parser(
        "send-scale",
        aliases=["scale"],
        help="send ascending scale with velocity ramp",
    )
    sp.add_argument("root", help="root note (e.g. C4)")
    sp.add_argument("--scale-type", choices=sorted(SCALES.keys()), default="major", help="scale type")
    sp.add_argument("--octaves", type=int, default=1, help="number of octaves (default: 1)")
    sp.add_argument("--ch", type=int, default=1, help="MIDI channel 1..16 (default: 1)")
    sp.add_argument("--vel-start", type=int, default=16, help="first velocity 1..127")
    sp.add_argument("--vel-end", type=int, default=127, help="last velocity 1..127")
    sp.add_argument("--gate-ms", type=int, default=130, help="note gate in milliseconds")
    sp.add_argument("--step-ms", type=int, default=200, help="time between note starts in milliseconds")
    sp.add_argument("--off-vel", type=int, default=0, help="note-off velocity 0..127")
    sp.set_defaults(func=_cmd_scale)

    return p


def main(argv: list[str] | None = None) -> int:
    try:
        _require_tools()
        parser = _build_parser()
        args = parser.parse_args(argv)
        return int(args.func(args))
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or "").strip()
        print(err or str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
