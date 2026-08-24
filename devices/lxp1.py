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
