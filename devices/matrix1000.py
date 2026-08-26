# devices/matrix1000.py — Oberheim Matrix-1000 device definition (pure data + builders)
#
# Ported 2026-08-24 from the JUCE VST Matrix1000NRPNController
# (mothership:~/projects/Matrix1000NRPNController/Source/Matrix1000Definition.h
# + PluginProcessor.cpp) — that project is the protocol authority.
#
# Messaging:
#   * Most params: NRPN — CC99=0, CC98=param#, CC6=value+64 (param 21 VCF
#     Cutoff is sent RAW, no +64 offset).
#   * Mod matrix slots: sysex F0 10 06 0B <slot> <src> <amount&0x7F> <dst> F7
#   * Bank select:      sysex F0 10 06 0A <bank 0-9> F7  (VST sent it 3x)
#   * Bank unlock:      sysex F0 10 06 0C F7 (append after transmissions —
#     lore from the SysExLibrarian project decisions log)
#   * Program select:   standard Program Change 0-99
#   * Store edit buf:   sysex F0 10 06 0E <program> <bank> 00 F7
#   * Request edit buf: sysex F0 10 06 04 04 00 F7 (reply = patch dump, TODO parse)

DEVICE_ID = "matrix1000"
DEVICE_NAME = "Matrix-1000"
DEFAULT_CHANNEL = 2   # rack instruments list: "Matrix-1k" = ch 2

CHOICES = {

    "kOffOnChoices": ['Off', 'On'],
    "kDcoSyncChoices": ['Off', 'Soft', 'Medium', 'Hard'],
    "kDco1WaveChoices": ['Off', 'Pulse', 'Saw', 'Pulse+Saw'],
    "kDco2WaveChoices": ['Off', 'Pulse', 'Saw', 'Pulse+Saw', 'Noise', 'Pulse+Noise', 'Saw+Noise', 'Pulse+Saw+Noise'],
    "kPitchBendVibratoChoices": ['Off', 'Pitch Bend', 'Vibrato', 'Both'],
    "kPortamentoChoices": ['Off', 'Portamento', 'Reserved', 'Reserved+Porta'],
    "kPortaKeyboardChoices": ['Off', 'Portamento', 'Keyboard', 'Both'],
    "kTrackKeyboardChoices": ['Off', 'Portamento', 'Track Kbd', 'Both'],
    "kRampTriggerChoices": ['Single', 'Multi', 'External', 'External Gated'],
    "kPortamentoModeChoices": ['Const Speed', 'Const Time', 'Exp', 'Exp Alt'],
    "kVoiceAssignChoices": ['Reassign', 'Rotate', 'Unison', 'Rob'],
    "kEnvelopeTriggerChoices": ['Off', 'Reset', 'Multi', 'Reset+Multi', 'External', 'Reset+Ext', 'Multi+Ext', 'All'],
    "kEnvelopeModeChoices": ['Normal', 'DADR', 'Freerun', 'Both'],
    "kEnvelopeLfoChoices": ['Off', 'Gated', 'LFO Trig', 'Both'],
    "kLfoWaveChoices": ['Triangle', 'Saw Up', 'Saw Down', 'Square', 'Random', 'Noise', 'S&H', 'Reserved'],
    "kLfoTriggerChoices": ['Freerun', 'Single', 'Multi', 'External'],
    "kTrackingSourceChoices": ['Env1', 'Env2', 'Env3', 'LFO1', 'LFO2', 'Vibrato', 'Ramp1', 'Ramp2', 'Keyboard', 'Portamento', 'Tracking Generator', 'Keyboard Gate', 'Velocity', 'Release Velocity', 'Aftertouch', 'Switch1 / Sustain', 'Switch2 / Sostenuto', 'Pitch Wheel', 'Mod Wheel', 'Lever3 / Breath'],
    "kModMatrixSourceChoices": ['Unused', 'Env1', 'Env2', 'Env3', 'LFO1', 'LFO2', 'Vibrato', 'Ramp1', 'Ramp2', 'Keyboard', 'Portamento', 'Tracking Generator', 'Keyboard Gate', 'Velocity', 'Release Velocity', 'Aftertouch', 'Switch1 / Sustain', 'Switch2 / Sostenuto', 'Pitch Wheel', 'Mod Wheel', 'Lever3 / Breath'],
    "kModMatrixDestinationChoices": ['Unused', 'DCO1 Frequency', 'DCO1 Pulse Width', 'DCO1 Waveshape', 'DCO2 Frequency', 'DCO2 Pulse Width', 'DCO2 Waveshape', 'Mix Level', 'VCF FM Amount', 'VCF Frequency', 'VCF Resonance', 'VCA1 Level', 'VCA2 Level', 'Env1 Delay', 'Env1 Attack', 'Env1 Decay', 'Env1 Release', 'Env1 Amplitude', 'Env2 Delay', 'Env2 Attack', 'Env2 Decay', 'Env2 Release', 'Env2 Amplitude', 'Env3 Delay', 'Env3 Attack', 'Env3 Decay', 'Env3 Release', 'Env3 Amplitude', 'LFO1 Speed', 'LFO1 Amplitude', 'LFO2 Speed', 'LFO2 Amplitude', 'Portamento Time'],
}

# (group, id, name, param#, min, max, default, choices_key_or_None)
PARAMS = [
    ("DCO 1", "dco1Frequency", "DCO1 Frequency", 0, 0, 63, 32, None),
    ("DCO 1", "dco1Lfo1Mod", "DCO1 LFO1 Mod", 1, -63, 63, 0, None),
    ("Global", "dcoSyncMode", "DCO Sync Mode", 2, 0, 3, 0, "kDcoSyncChoices"),
    ("DCO 1", "dco1PulseWidth", "DCO1 Pulse Width", 3, 0, 63, 32, None),
    ("DCO 1", "dco1PwLfo2Mod", "DCO1 PW LFO2 Mod", 4, -63, 63, 0, None),
    ("DCO 1", "dco1WaveShape", "DCO1 Waveshape", 5, 0, 63, 0, None),
    ("DCO 1", "dco1Waveform", "DCO1 Waveform", 6, 0, 3, 0, "kDco1WaveChoices"),
    ("DCO 1", "dco1FixedMod", "DCO1 Fixed Mod", 7, 0, 3, 0, "kPitchBendVibratoChoices"),
    ("DCO 1", "dco1KeyboardMod", "DCO1 Kbd Mod", 8, 0, 3, 0, "kPortamentoChoices"),
    ("DCO 1", "dco1Click", "DCO1 Click", 9, 0, 1, 0, "kOffOnChoices"),
    ("DCO 2", "dco2Frequency", "DCO2 Frequency", 10, 0, 63, 32, None),
    ("DCO 2", "dco2Lfo1Mod", "DCO2 LFO1 Mod", 11, -63, 63, 0, None),
    ("DCO 2", "dco2Detune", "DCO2 Detune", 12, -31, 31, 0, None),
    ("DCO 2", "dco2PulseWidth", "DCO2 Pulse Width", 13, 0, 63, 32, None),
    ("DCO 2", "dco2PwLfo2Mod", "DCO2 PW LFO2 Mod", 14, -63, 63, 0, None),
    ("DCO 2", "dco2WaveShape", "DCO2 Waveshape", 15, 0, 63, 0, None),
    ("DCO 2", "dco2Waveform", "DCO2 Waveform", 16, 0, 7, 0, "kDco2WaveChoices"),
    ("DCO 2", "dco2FixedMod", "DCO2 Fixed Mod", 17, 0, 3, 0, "kPitchBendVibratoChoices"),
    ("DCO 2", "dco2KeyboardMod", "DCO2 Kbd Mod", 18, 0, 3, 0, "kPortaKeyboardChoices"),
    ("DCO 2", "dco2Click", "DCO2 Click", 19, 0, 1, 0, "kOffOnChoices"),
    ("Global", "dcoMix", "DCO Mix", 20, 0, 63, 32, None),
    ("VCF", "vcfCutoff", "VCF Cutoff", 21, 0, 127, 64, None),
    ("VCF", "vcfEnv1Mod", "VCF Env1 Mod", 22, -63, 63, 0, None),
    ("VCF", "vcfAftertouchMod", "VCF Aftertouch Mod", 23, -63, 63, 0, None),
    ("VCF", "vcfResonance", "VCF Resonance", 24, 0, 63, 0, None),
    ("VCF", "vcfFixedMod", "VCF Fixed Mod", 25, 0, 3, 0, "kPitchBendVibratoChoices"),
    ("VCF", "vcfKeyboardMod", "VCF Kbd Mod", 26, 0, 3, 0, "kTrackKeyboardChoices"),
    ("VCA", "vca1Level", "VCA1 Level", 27, 0, 63, 32, None),
    ("VCA", "vca1VelocityMod", "VCA1 Velocity Mod", 28, -63, 63, 0, None),
    ("VCA", "vca2Env2Mod", "VCA2 Env2 Mod", 29, -63, 63, 0, None),
    ("VCF", "vcfFmAmount", "VCF FM", 30, 0, 63, 0, None),
    ("VCF", "vcfFmEnv3Mod", "VCF FM Env3 Mod", 31, -63, 63, 0, None),
    ("VCF", "vcfFmAftertouchMod", "VCF FM Aftertouch Mod", 32, -63, 63, 0, None),
    ("Tracking", "trackingGeneratorInput", "Tracking Generator Input", 33, 1, 20, 1, "kTrackingSourceChoices"),
    ("Tracking", "trackingPoint1", "Tracking Point 1", 34, 0, 63, 0, None),
    ("Tracking", "trackingPoint2", "Tracking Point 2", 35, 0, 63, 16, None),
    ("Tracking", "trackingPoint3", "Tracking Point 3", 36, 0, 63, 32, None),
    ("Tracking", "trackingPoint4", "Tracking Point 4", 37, 0, 63, 48, None),
    ("Tracking", "trackingPoint5", "Tracking Point 5", 38, 0, 63, 63, None),
    ("Ramps / Portamento", "ramp1Speed", "Ramp1 Speed", 40, 0, 63, 32, None),
    ("Ramps / Portamento", "ramp1TriggerMode", "Ramp1 Trigger", 41, 0, 3, 0, "kRampTriggerChoices"),
    ("Ramps / Portamento", "ramp2Speed", "Ramp2 Speed", 42, 0, 63, 32, None),
    ("Ramps / Portamento", "ramp2TriggerMode", "Ramp2 Trigger", 43, 0, 3, 0, "kRampTriggerChoices"),
    ("Ramps / Portamento", "portamentoAmount", "Portamento", 44, 0, 63, 0, None),
    ("Ramps / Portamento", "portamentoVelocityMod", "Portamento Velocity Mod", 45, -63, 63, 0, None),
    ("Ramps / Portamento", "portamentoMode", "Portamento Mode", 46, 0, 3, 0, "kPortamentoModeChoices"),
    ("Ramps / Portamento", "portamentoLegato", "Portamento Legato", 47, 0, 1, 0, "kOffOnChoices"),
    ("Ramps / Portamento", "voiceAssign", "Voice Assign", 48, 0, 3, 0, "kVoiceAssignChoices"),
    ("Envelope 1", "env1Delay", "Env1 Delay", 50, 0, 63, 0, None),
    ("Envelope 1", "env1Attack", "Env1 Attack", 51, 0, 63, 0, None),
    ("Envelope 1", "env1Decay", "Env1 Decay", 52, 0, 63, 32, None),
    ("Envelope 1", "env1Sustain", "Env1 Sustain", 53, 0, 63, 63, None),
    ("Envelope 1", "env1Release", "Env1 Release", 54, 0, 63, 32, None),
    ("Envelope 1", "env1Amplitude", "Env1 Amplitude", 55, 0, 63, 63, None),
    ("Envelope 1", "env1VelocityMod", "Env1 Velocity Mod", 56, -63, 63, 0, None),
    ("Envelope 1", "env1TriggerMode", "Env1 Trigger", 57, 0, 7, 0, "kEnvelopeTriggerChoices"),
    ("Envelope 1", "env1Mode", "Env1 Mode", 58, 0, 3, 0, "kEnvelopeModeChoices"),
    ("Envelope 1", "env1LfoMode", "Env1 LFO Trigger", 59, 0, 3, 0, "kEnvelopeLfoChoices"),
    ("Envelope 2", "env2Delay", "Env2 Delay", 60, 0, 63, 0, None),
    ("Envelope 2", "env2Attack", "Env2 Attack", 61, 0, 63, 0, None),
    ("Envelope 2", "env2Decay", "Env2 Decay", 62, 0, 63, 32, None),
    ("Envelope 2", "env2Sustain", "Env2 Sustain", 63, 0, 63, 63, None),
    ("Envelope 2", "env2Release", "Env2 Release", 64, 0, 63, 32, None),
    ("Envelope 2", "env2Amplitude", "Env2 Amplitude", 65, 0, 63, 63, None),
    ("Envelope 2", "env2VelocityMod", "Env2 Velocity Mod", 66, -63, 63, 0, None),
    ("Envelope 2", "env2TriggerMode", "Env2 Trigger", 67, 0, 7, 0, "kEnvelopeTriggerChoices"),
    ("Envelope 2", "env2Mode", "Env2 Mode", 68, 0, 3, 0, "kEnvelopeModeChoices"),
    ("Envelope 2", "env2LfoMode", "Env2 LFO Trigger", 69, 0, 3, 0, "kEnvelopeLfoChoices"),
    ("Envelope 3", "env3Delay", "Env3 Delay", 70, 0, 63, 0, None),
    ("Envelope 3", "env3Attack", "Env3 Attack", 71, 0, 63, 0, None),
    ("Envelope 3", "env3Decay", "Env3 Decay", 72, 0, 63, 32, None),
    ("Envelope 3", "env3Sustain", "Env3 Sustain", 73, 0, 63, 63, None),
    ("Envelope 3", "env3Release", "Env3 Release", 74, 0, 63, 32, None),
    ("Envelope 3", "env3Amplitude", "Env3 Amplitude", 75, 0, 63, 63, None),
    ("Envelope 3", "env3VelocityMod", "Env3 Velocity Mod", 76, -63, 63, 0, None),
    ("Envelope 3", "env3TriggerMode", "Env3 Trigger", 77, 0, 7, 0, "kEnvelopeTriggerChoices"),
    ("Envelope 3", "env3Mode", "Env3 Mode", 78, 0, 3, 0, "kEnvelopeModeChoices"),
    ("Envelope 3", "env3LfoMode", "Env3 LFO Trigger", 79, 0, 3, 0, "kEnvelopeLfoChoices"),
    ("LFO 1", "lfo1Speed", "LFO1 Speed", 80, 0, 63, 32, None),
    ("LFO 1", "lfo1AftertouchMod", "LFO1 Aftertouch Mod", 81, -63, 63, 0, None),
    ("LFO 1", "lfo1Wave", "LFO1 Wave", 82, 0, 7, 0, "kLfoWaveChoices"),
    ("LFO 1", "lfo1RetriggerPoint", "LFO1 Retrigger Point", 83, 0, 31, 0, None),
    ("LFO 1", "lfo1Amplitude", "LFO1 Amplitude", 84, 0, 63, 32, None),
    ("LFO 1", "lfo1Ramp1Mod", "LFO1 Ramp1 Mod", 85, -63, 63, 0, None),
    ("LFO 1", "lfo1TriggerMode", "LFO1 Trigger", 86, 0, 3, 0, "kLfoTriggerChoices"),
    ("LFO 1", "lfo1Lag", "LFO1 Lag", 87, 0, 1, 0, "kOffOnChoices"),
    ("LFO 1", "lfo1SampleHoldSource", "LFO1 S&H Source", 88, 1, 20, 1, "kTrackingSourceChoices"),
    ("LFO 2", "lfo2Speed", "LFO2 Speed", 90, 0, 63, 32, None),
    ("LFO 2", "lfo2KeyboardMod", "LFO2 Keyboard Mod", 91, -63, 63, 0, None),
    ("LFO 2", "lfo2Wave", "LFO2 Wave", 92, 0, 7, 0, "kLfoWaveChoices"),
    ("LFO 2", "lfo2RetriggerPoint", "LFO2 Retrigger Point", 93, 0, 31, 0, None),
    ("LFO 2", "lfo2Amplitude", "LFO2 Amplitude", 94, 0, 63, 32, None),
    ("LFO 2", "lfo2Ramp2Mod", "LFO2 Ramp2 Mod", 95, -63, 63, 0, None),
    ("LFO 2", "lfo2TriggerMode", "LFO2 Trigger", 96, 0, 3, 0, "kLfoTriggerChoices"),
    ("LFO 2", "lfo2Lag", "LFO2 Lag", 97, 0, 1, 0, "kOffOnChoices"),
    ("LFO 2", "lfo2SampleHoldSource", "LFO2 S&H Source", 98, 1, 20, 1, "kTrackingSourceChoices"),
]

MOD_SOURCES = CHOICES["kModMatrixSourceChoices"]
MOD_DESTS = CHOICES["kModMatrixDestinationChoices"]
MOD_SLOTS = 10

_OB = (0x10, 0x06)   # Oberheim manufacturer, Matrix family


# Stock firmware (v1.11) has NRPN handling disabled by a firmware bug; it only
# works on Bob Grieb's / untergeek's v1.20+. Oberheim's own sysex edit command
# works on every firmware: F0 10 06 06 <param> <value> F7, value raw 7-bit
# (signed params as 7-bit two's complement, VCF cutoff 0-127).
EDIT_MODE_DEFAULT = "sysex"   # "sysex" | "nrpn" | "both"


def edit_param_sysex(param_num, value):
    return (0x10, 0x06, 0x06, param_num & 0x7F, int(value) & 0x7F)


def nrpn_cc_messages(param_num, value, channel):
    """Return [(cc, val), ...] for one NRPN parameter edit. channel unused
    here (caller owns channel); value is the ACTUAL param value (may be
    negative); encoding per the VST: +64 offset except param 21."""
    enc = value if param_num == 21 else value + 64
    return [(99, 0), (98, param_num & 0x7F), (6, enc & 0x7F)]


def mod_matrix_sysex(slot, source, amount, dest):
    """amount -63..63, two's-complement into 7 bits like the VST."""
    return (0x10, 0x06, 0x0B, slot & 0x7F, source & 0x7F, amount & 0x7F, dest & 0x7F)


def bank_select_sysex(bank):
    return (0x10, 0x06, 0x0A, bank & 0x7F)


def bank_unlock_sysex():
    return (0x10, 0x06, 0x0C)


def store_edit_buffer_sysex(program, bank):
    return (0x10, 0x06, 0x0E, program & 0x7F, bank & 0x7F, 0x00)


def request_edit_buffer_sysex():
    return (0x10, 0x06, 0x04, 0x04, 0x00)


def groups():
    """Ordered unique group names."""
    seen, order = set(), []
    for g, *_ in PARAMS:
        if g not in seen:
            seen.add(g)
            order.append(g)
    return order


def params_in_group(group):
    return [p for p in PARAMS if p[0] == group]


# ---------------------------------------------------------------------------
# Patch dump (edit buffer / single patch) — ported from the VST's
# tryDecodePatchDump/applyPatchBytesToParameters (PluginProcessor.cpp).
# Frame: F0 10 06 <01|0D> <pp> <268 nibbles, LO nibble first> <sum&0x7F> F7
# Patch bytes: 0-7 name, 8-103 parameters via PATCH_BYTE_TO_PARAM, 104-133
# mod matrix (10 x source/amount/dest, amount signed 8-bit).
# ---------------------------------------------------------------------------

PATCH_BYTE_TO_PARAM = [
    -1, -1, -1, -1, -1, -1, -1, -1,
    48, 0, 5, 3, 7, 6, 10, 15, 13, 17, 16, 12, 20, 8, 9, 18, 19, 2, 21, 24, 25, 26, 30, 27,
    44, 46, 47, 80, 86, 87, 82, 83, 88, 84, 90, 96, 97, 92, 93, 98, 94, 57, 50, 51, 52, 53, 54, 55,
    59, 58, 67, 60, 61, 62, 63, 64, 65, 69, 68, 77, 70, 71, 72, 73, 74, 75, 79, 78, 33, 34, 35, 36,
    37, 38, 40, 41, 42, 43, 1, 4, 11, 14, 22, 23, 28, 29, 56, 66, 76, 85, 95, 45, 31, 32, 81, 91,
]

_SIGNED_PARAMS = {num for (_, _, _, num, mn, _, _, _) in PARAMS if mn < 0}


def _signed8(v):
    return v - 256 if v >= 128 else v


def decode_patch_dump(data):
    """Decode a patch dump. `data` = mido sysex bytes (F0/F7 excluded) or a
    full frame. Returns {"values": {param#: actual}, "mod": {slot: [s,a,d]},
    "name": str, "patch": int} or None if not a valid dump."""
    b = list(data)
    if b and b[0] == 0xF0:
        b = b[1:]
    if b and b[-1] == 0xF7:
        b = b[:-1]
    # b: 10 06 cmd pp <268 nibbles> sum
    if len(b) < 4 + 268 + 1 or b[0] != 0x10 or b[1] != 0x06:
        return None
    cmd = b[2]
    if cmd not in (0x01, 0x0D):
        return None
    nib = b[4:4 + 268]
    if any(n > 0x0F for n in nib):
        return None
    patch = []
    checksum = 0
    for i in range(134):
        v = nib[2 * i] | (nib[2 * i + 1] << 4)   # LO nibble first
        patch.append(v)
        checksum += v
    if (checksum & 0x7F) != b[4 + 268]:
        return None
    values = {}
    for idx, pnum in enumerate(PATCH_BYTE_TO_PARAM):
        if pnum < 0:
            continue
        raw = patch[idx]
        values[pnum] = _signed8(raw) if pnum in _SIGNED_PARAMS else raw
    mod = {}
    for slot in range(MOD_SLOTS):
        off = 104 + slot * 3
        mod[slot] = [
            max(0, min(len(MOD_SOURCES) - 1, patch[off])),
            max(-63, min(63, _signed8(patch[off + 1]))),
            max(0, min(len(MOD_DESTS) - 1, patch[off + 2])),
        ]
    name = "".join(chr(c & 0x7F) if 32 <= (c & 0x7F) < 127 else " "
                   for c in patch[0:8]).strip()
    return {"values": values, "mod": mod, "name": name, "patch": b[3]}


def build_patch_dump(values, mod, name="PULLTEST", patch_num=0, cmd=0x0D):
    """Inverse of decode_patch_dump (testing / synthetic injection). Returns
    mido-style sysex data (F0/F7 excluded)."""
    patchb = [0] * 134
    for i, ch in enumerate(str(name)[:8].ljust(8)):
        patchb[i] = ord(ch) & 0x7F
    for idx, pnum in enumerate(PATCH_BYTE_TO_PARAM):
        if pnum < 0:
            continue
        v = int(values.get(pnum, 0))
        patchb[idx] = v & 0xFF
    for slot in range(MOD_SLOTS):
        off = 104 + slot * 3
        s, a, d = (mod.get(slot) or [0, 0, 0])
        patchb[off] = int(s) & 0xFF
        patchb[off + 1] = int(a) & 0xFF
        patchb[off + 2] = int(d) & 0xFF
    out = [0x10, 0x06, cmd, patch_num & 0x7F]
    for v in patchb:
        out.append(v & 0x0F)
        out.append((v >> 4) & 0x0F)
    out.append(sum(patchb) & 0x7F)
    return tuple(out)
