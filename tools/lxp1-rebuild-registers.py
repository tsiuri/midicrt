#!/usr/bin/env python3
"""Rebuild an LXP-1's 128 user registers over MIDI after battery loss
(when the front-panel factory reset isn't possible).

Registers n = factory-like setup for preset (n % 16): registers 0-15 are
the 16 "presets" in program-table order, repeating up to 127.  Each register
is written by loading an Active Setup image (class 0n) and then storing it
with the store-register event (70h).  Requires the unit's MIDI jack jumpered
as OUT + cabled to the interface IN for verification (pull of a sample).

Usage: lxp1-rebuild-registers.py [--channel N] [--verify-only]
"""
import argparse, sys, time
sys.path.insert(0, "/home/billie/codex/midicrt")
import mido
from devices import lxp1 as d

ap = argparse.ArgumentParser()
ap.add_argument("--channel", type=int, default=1)
ap.add_argument("--verify-only", action="store_true")
ap.add_argument("--iface", default="UX16")
a = ap.parse_args()
CH = a.channel
out = mido.open_output([n for n in mido.get_output_names() if a.iface in n][0])
inp = mido.open_input([n for n in mido.get_input_names() if a.iface in n][0])


def sx(data, wait=0.12):
    out.send(mido.Message("sysex", data=list(data))); time.sleep(wait)


def pull():
    for _ in inp.iter_pending():
        pass
    sx(d.request_active_setup_sysex(CH), 0)
    t = time.time() + 2.0
    while time.time() < t:
        for m in inp.iter_pending():
            if m.type == "sysex":
                dec = d.decode_setup_dump(m.data)
                if dec:
                    return dec
        time.sleep(0.02)
    return None


if not a.verify_only:
    for reg in range(128):
        preset = reg % 16
        sx(d.factory_like_image(preset, CH), 0.25)   # load as active setup
        sx(d.event_sysex(0x70, reg, CH), 0.25)        # store to register
        if reg % 16 == 15:
            print(f"  stored registers {reg-15:3d}-{reg:3d}")
    print("all 128 registers written")

def check(reg):
    out.send(mido.Message("program_change", program=reg, channel=CH - 1)); time.sleep(0.5)
    dec = pull()
    want = d.PRESETS[reg % 16]
    return bool(dec and dec["program"] == want[1]
                and dec["name"].startswith(want[0].upper()[:8])), dec, want


print("verifying ALL 128 registers via Program Change + pull (repairing misses):")
bad = []
for reg in range(128):
    good, dec, want = check(reg)
    if not good:
        bad.append(reg)
        print(f"  reg {reg:3d}: BAD -> {dec and (dec['program'], dec['name'])} expected {want}; rewriting")
        for attempt in range(3):
            sx(d.factory_like_image(reg % 16, CH), 0.5)
            sx(d.event_sysex(0x70, reg, CH), 0.5)
            good, dec, want = check(reg)
            if good:
                print(f"           repaired on attempt {attempt+1}")
                bad.remove(reg)
                break
print(f"done: {128-len(bad)}/128 registers verified" + (f"; still bad: {bad}" if bad else ""))
out.send(mido.Message("program_change", program=0, channel=CH - 1))
