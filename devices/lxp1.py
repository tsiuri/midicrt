# devices/lxp1.py — Lexicon LXP-1 device definition (pure data + builders)
#
# Protocol authority: docs/lxp1-protocol.md (owner's manual rev 1.2 ch.4 +
# empirical findings 2026-08-24). All builders return the sysex body BETWEEN
# F0 and F7 (mido convention), except program_change which is (status-less)
# semantic info for the caller.

DEVICE_ID = "lxp1"
DEVICE_NAME = "LXP-1"
DEFAULT_CHANNEL = 1


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

PRESETS = [
    ("Small 1", 1), ("Small 2", 1), ("Medium 1", 1), ("Medium 2", 1),
    ("Large 1", 1), ("Large 2", 1), ("Hall D", 1), ("Hall B", 1),
    ("Plate D", 2), ("Plate B", 2), ("Inverse", 6), ("Gate", 7),
    ("Chorus 1", 3), ("Chorus 2", 5), ("Delay 1", 8), ("Delay 2", 4),
]

PARAM_SETUP = 64
EVENT_STORE_REGISTER = 0x70


def fields_for_program(program):
    """Editable fields for an algorithm: its params + Input Level."""
    return list(ALGORITHMS[program][1]) + [_INPUT_LEVEL]


def step_to_value16(param, step):
    steps = param["steps"]
    frac = 0.0 if steps <= 1 else step / (steps - 1)
    if param["bipolar"]:
        return 0x4000 + round(frac * 0x7FFF)
    return 0x8000 + round(frac * 0x3FFF)


def param_adjust_sysex(param_num, value16, channel, klass="packed"):
    """channel 1-16. klass 'packed' (0x2n, PC1600-proven) or 'nibble' (0x5n)."""
    n = (channel - 1) & 0x0F
    value16 = max(0, min(0xFFFF, int(value16)))
    if klass == "packed":
        a = value16 & 0xFF
        b = (value16 >> 8) & 0xFF
        return (0x06, 0x02, 0x20 | n, param_num & 0x7F,
                ((b >> 7) << 1) | (a >> 7), a & 0x7F, b & 0x7F)
    return (0x06, 0x02, 0x50 | n, param_num & 0x7F,
            (value16 >> 12) & 0x0F, (value16 >> 8) & 0x0F,
            (value16 >> 4) & 0x0F, value16 & 0x0F)


def event_sysex(event, p, channel):
    n = (channel - 1) & 0x0F
    return (0x06, 0x02, 0x60 | n, event & 0x7F, p & 0x7F)


def setup_select_sysex(setup, channel, klass="packed"):
    """setup 0-127 = registers, 128-144 = factory presets 0-15."""
    return param_adjust_sysex(PARAM_SETUP, setup, channel, klass)


# ---------------------------------------------------------------------------
# Factory preset defaults from the owner's manual program tables (p.2-9/2-10):
# the two front-panel knob parameters (param 0 / param 1) as DISPLAY values,
# plus Effects Level (param 2) which the manual says is always 100% in factory
# presets. Other params are unknown until a dump is pulled.
#   preset index -> {param#: display_value}
# ---------------------------------------------------------------------------
FACTORY_DEFAULTS = {
    0:  {0: 0.8, 1: 16.0, 2: 100},    # Small 1
    1:  {0: 1.2, 1: 16.0, 2: 100},    # Small 2
    2:  {0: 1.2, 1: 33.0, 2: 100},    # Medium 1
    3:  {0: 1.6, 1: 33.0, 2: 100},    # Medium 2
    4:  {0: 2.2, 1: 33.0, 2: 100},    # Large 1
    5:  {0: 2.4, 1: 33.0, 2: 100},    # Large 2
    6:  {0: 1.6, 1: 33.0, 2: 100},    # Hall D
    7:  {0: 2.6, 1: 33.0, 2: 100},    # Hall B
    8:  {0: 1.8, 1: 33.0, 2: 100},    # Plate D
    9:  {0: 1.7, 1: 0.0, 2: 100},     # Plate B
    10: {0: 0.3 * 32, 8: 0.0, 2: 100},   # Inverse: size .3s (of 1..32 scale), predelay 0ms
    11: {0: 250.0, 8: 0.0, 2: 100},   # Gate: time .25s, predelay 0ms
    12: {0: 0.0, 1: 4.1, 2: 100},     # Chorus 1: feedback 0%, depth 4.1ms
    13: {0: 93.0, 1: 0.0, 2: 100},    # Chorus 2: resonance 93%, tuning 0 semi
    14: {0: 40.0, 1: 279.0, 2: 100},  # Delay 1: feedback 40%, group delay 279ms
    15: {0: 25.0, 1: 9.8, 2: 100},    # Delay 2: feedback 25%, delay spacing 9.8ms
}


def display_to_step(param, disp):
    lo, hi = param["dmin"], param["dmax"]
    if hi == lo:
        return 0
    frac = (float(disp) - lo) / (hi - lo)
    frac = max(0.0, min(1.0, frac))
    return round(frac * (param["steps"] - 1))


def factory_default_steps(preset):
    """{param#: step} for a factory preset, in the preset's own algorithm."""
    program = PRESETS[preset][1]
    out = {}
    for p in fields_for_program(program):
        d = FACTORY_DEFAULTS.get(preset, {}).get(p["num"])
        if d is not None:
            out[p["num"]] = display_to_step(p, d)
    return out


# ---------------------------------------------------------------------------
# Dumps: Active Setup Data (class 0n) / Stored Register (class 1n).
# 7-in-8 packing (manual 4-9/4-10): 8 wire bytes -> 7 data bytes; the first
# wire byte carries the MSBs (0 g7 f7 e7 d7 c7 b7 a7), then a6..a0 ... g6..g0.
# 49 unpacked bytes: [0] prog ID, [1..20] params 0-9 as 16-bit little-endian,
# [21..36] name, [37..40] patch sources, [41..44] dests, [45..48] scale factors.
# Requires the unit's MIDI jack jumpered as OUT (factory = THRU) to ever be
# transmitted; the request is class 3n event 60h.
# ---------------------------------------------------------------------------

def unpack_7in8(packed):
    out = []
    packed = list(packed)
    for i in range(0, len(packed), 8):
        chunk = packed[i:i + 8]
        if len(chunk) < 2:
            break
        msbs = chunk[0]
        for j, b in enumerate(chunk[1:]):
            out.append((b & 0x7F) | (((msbs >> j) & 1) << 7))
    return out


def pack_7in8(data):
    out = []
    data = list(data)
    for i in range(0, len(data), 7):
        chunk = data[i:i + 7]
        msbs = 0
        for j, b in enumerate(chunk):
            msbs |= ((b >> 7) & 1) << j
        out.append(msbs)
        out.extend(b & 0x7F for b in chunk)
    return out


def request_active_setup_sysex(channel):
    n = (channel - 1) & 0x0F
    return (0x06, 0x02, 0x30 | n, 0x60, 0x00)


def decode_setup_dump(data):
    """Decode an Active Setup (0n) or Stored Register (1n) dump. `data` =
    mido sysex bytes (F0/F7 excluded) or full frame. Returns
    {"program": pgm_id, "values16": {param#: v16}, "name": str,
     "register": int|None} or None."""
    b = list(data)
    if b and b[0] == 0xF0:
        b = b[1:]
    if b and b[-1] == 0xF7:
        b = b[:-1]
    if len(b) < 4 or b[0] != 0x06 or b[1] != 0x02:
        return None
    klass = b[2] >> 4
    if klass == 0x0:
        count_idx, register = 3, None
    elif klass == 0x1:
        count_idx, register = 4, b[3]
    else:
        return None
    count = b[count_idx]
    packed = b[count_idx + 1:count_idx + 1 + count]
    if len(packed) != count or count != 56:
        return None
    if (sum(packed) & 0x7F) != b[count_idx + 1 + count]:
        return None
    raw = unpack_7in8(packed)[:49]
    if len(raw) < 49:
        return None
    values16 = {}
    for p in range(10):
        lo, hi = raw[1 + 2 * p], raw[2 + 2 * p]
        values16[p] = lo | (hi << 8)
    name = "".join(chr(c) if 32 <= c < 127 else " " for c in raw[21:37]).strip()
    return {"program": raw[0], "values16": values16, "name": name,
            "register": register}


def build_setup_dump(program, values16, channel=1, name="TEST", register=None):
    """Inverse of decode_setup_dump (tests / synthetic injection)."""
    raw = [0] * 49
    raw[0] = program & 0xFF
    for p in range(10):
        v = int(values16.get(p, 0x8000)) & 0xFFFF
        raw[1 + 2 * p] = v & 0xFF
        raw[2 + 2 * p] = v >> 8
    for i, ch in enumerate(str(name)[:16].ljust(16)):
        raw[21 + i] = ord(ch) & 0x7F
    packed = pack_7in8(raw)
    n = (channel - 1) & 0x0F
    if register is None:
        head = [0x06, 0x02, 0x00 | n, len(packed)]
    else:
        head = [0x06, 0x02, 0x10 | n, register & 0x7F, len(packed)]
    return tuple(head + packed + [sum(packed) & 0x7F])


def value16_to_step(param, v16):
    if param["bipolar"]:
        frac = (v16 - 0x4000) / 0x7FFF
    else:
        frac = (v16 - 0x8000) / 0x3FFF
    frac = max(0.0, min(1.0, frac))
    return round(frac * (param["steps"] - 1))


# ---------------------------------------------------------------------------
# Factory-LIKE setup images: for rebuilding a battery-wiped unit over MIDI
# when the front-panel factory reset isn't possible. Program ID + the
# manual's documented knob/FX values per preset; the remaining params get
# musically sensible defaults (documented as approximations, not factory).
# ---------------------------------------------------------------------------

_ALG_FILL = {   # param -> value16 defaults per algorithm for undocumented params
    1: {3: 0x8000, 4: 0xB000, 5: 0xA000, 6: 0x8000, 7: 0xB800},          # reverbs
    2: {3: 0x8000, 4: 0xB000, 5: 0xA000, 6: 0x8000, 7: 0xB800},
    3: {3: 0x8000, 4: 0x8800, 5: 0xB000, 6: 0x8000, 7: 0x8800, 8: 0x9000},  # chorus 1
    4: {1: 0x9000, 3: 0x8000, 4: 0x9000, 5: 0x9000, 7: 0xB000, 8: 0xB800},  # delay 2
    5: {3: 0x8800, 4: 0x8400, 5: 0x8800, 6: 0xB000, 7: 0x8800, 8: 0x9000, 9: 0x8000},  # chorus 2
    6: {4: 0xB000, 5: 0xBFFF, 6: 0x8000, 7: 0xB800},                      # inverse
    7: {4: 0xB000, 5: 0xBFFF, 6: 0x8000, 7: 0xB800},                      # gate
    8: {3: 0xB000, 4: 0x9000, 5: 0x9000, 6: 0x8000, 7: 0xB800, 8: 0x9000},  # delay 1
}


def factory_like_values16(preset):
    """{param#: value16} for a preset: documented values + algorithm fill."""
    program = PRESETS[preset][1]
    out = {p: 0x8000 for p in range(10)}
    out.update(_ALG_FILL.get(program, {}))
    for p in fields_for_program(program):
        step = factory_default_steps(preset).get(p["num"])
        if step is not None:
            out[p["num"]] = step_to_value16(p, step)
    out[2] = 0xBFFF   # FX level 100% in every factory preset
    return out


def factory_like_image(preset, channel=1, register=None):
    program = PRESETS[preset][1]
    return build_setup_dump(program, factory_like_values16(preset), channel=channel,
                            name=PRESETS[preset][0].upper()[:16], register=register)
