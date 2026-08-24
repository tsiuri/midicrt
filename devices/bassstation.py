# devices/bassstation.py — Novation Bass Station Rack (pure data + builders)
#
# Protocol authority: Bass Station Rack owner's manual (polynominal.com scan,
# MIDI Control chapter p.18 + MIDI Implementation Chart p.21, OCR'd
# 2026-08-24).  The original rack exposes ONLY the filter and envelope
# controls over MIDI CC — oscillator/LFO/mixer parameters are front-panel
# only (that's the hardware, not a port gap).  Sysex (F0 00 20 29) is a
# manual front-panel voice dump with no request command (SysExLibrarian
# lore), so there is nothing to edit over sysex either.
#
#   CC 105-107  Filter Frequency / Resonance / Mod Depth
#   CC 108-112  Env 1 Attack / Decay / Sustain / Release / Velocity
#   CC 114-118  Env 2 Attack / Decay / Sustain / Release / Velocity
#   CC 1 / 2 / 7  Mod Wheel / Breath / Volume (recognised, not transmitted)
#   Program Change 0-99 (00-39 factory, 40-99 user)
#   Pitch bend recognised 0-12 semitones.

DEVICE_ID = "bassstation"
DEVICE_NAME = "Bass Station Rack"
DEFAULT_CHANNEL = 3   # rack instruments list: "BassStaRack" = ch 3

# (group, id, label, cc)  — all values 0-127
PARAMS = [
    ("Filter", "filterFrequency", "Frequency", 105),
    ("Filter", "filterResonance", "Resonance", 106),
    ("Filter", "filterModDepth", "Mod Depth", 107),
    ("Envelope 1", "env1Attack", "Attack", 108),
    ("Envelope 1", "env1Decay", "Decay", 109),
    ("Envelope 1", "env1Sustain", "Sustain", 110),
    ("Envelope 1", "env1Release", "Release", 111),
    ("Envelope 1", "env1Velocity", "Velocity", 112),
    ("Envelope 2", "env2Attack", "Attack", 114),
    ("Envelope 2", "env2Decay", "Decay", 115),
    ("Envelope 2", "env2Sustain", "Sustain", 116),
    ("Envelope 2", "env2Release", "Release", 117),
    ("Envelope 2", "env2Velocity", "Velocity", 118),
    ("Performance", "modWheel", "Mod Wheel", 1),
    ("Performance", "breath", "Breath", 2),
    ("Performance", "volume", "Volume", 7),
]

# Factory program names 00-39 (manual p.22); 40-99 are user slots.
FACTORY_PROGRAMS = [
    "MOOGBASS", "WOW BASS", "JACKO BASS", "SOFT BASS", "ELECTRIC BASS",
    "BIRDLAND BASS", "PERCUSSIVE BASS", "EOW BASS", "POWER BASS",
    "FREAKPOWER BASS", "TB3O3AUTOGLIDE BASS", "SPITSINE BASS",
    "TB303 EOW BASS", "TB303 SQUARE BASS", "THUD BASS", "AMBIENT TB303",
    "TRANCE 1", "TRANCE 2", "SPIT", "RAINMAN", "YAZOO LEAD", "ORGAN BASS",
    "CLAVY LEAD", "PLUCK LEAD", "LFO FILTER FADE BASS", "SQUARE BASS",
    "WOW BASS 2", "WOWEOW BASS", "HARD SYNC LEAD", "SQUARE PORTA LEAD",
    "SYNC 0 LEAD", "SYNC I LEAD", "PT POWER LEAD", "LOVE DON'T LEAD",
    "DUCK LEAD", "OLAVE LEAD", "WHISTLE LEAD", "YAZ2LEAD", "SYNC3LEAD",
    "RESONANT LEAD",
]


def program_name(pp):
    if 0 <= pp < len(FACTORY_PROGRAMS):
        return FACTORY_PROGRAMS[pp]
    return f"User {pp}"


def cc_for(param_id):
    for _, pid, _, cc in PARAMS:
        if pid == param_id:
            return cc
    raise ValueError(f"unknown param {param_id}")


def groups():
    seen, order = set(), []
    for g, *_ in PARAMS:
        if g not in seen:
            seen.add(g)
            order.append(g)
    return order


def params_in_group(group):
    return [p for p in PARAMS if p[0] == group]
