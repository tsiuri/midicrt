# devices/tg77.py — Yamaha TG77 device definition (pure data + builders)
#
# Ported 2026-08-24, byte-exactly, from the JUCE VST "TG77ControllerVST3"
# (scratchpad sources: TG77ParameterDefinitions.h + tg77processor.cpp) —
# that VST is the protocol authority for every address and transform here.
#
# Messaging (parameter change):
#   Full wire frame (11 bytes):
#     F0 43 (0x10|dev) 34 <base> <layer> 00 <address> <msb> <lsb> F7
#   mido-style sysex data (excluding F0/F7) is the 9-byte body returned by
#   message():  (0x43, 0x10|dev, 0x34, base, layer, 0x00, address, msb, lsb)
#
# Layer-byte semantics (from tg77processor.cpp):
#   * getAfmLayerByte()       = element_slot * 0x20   (element slot 0-3)
#   * getSlotFilterBaseByte() = element_slot * 0x20   (same formula)
#   * Filter messages use base 0x09 with layer = slot_base + filter_offset:
#       AFM  bank filter offset = filter_select        (0 or 1)
#       AWM  bank filter offset = filter_select + 3
#       AFM  common filter offset = 0x02
#       AWM  common filter offset = 0x05
#   * AFM operator messages use base = OPERATOR_INFOS[op].base_address,
#     layer = afm layer byte.
#   * AWM element messages use base 0x07, layer = afm layer byte
#     (EXCEPT awmPitchEgVelocity, whose layer is literal 0x00 in the VST).
#   * Global (AFM main/sub LFO) messages use base 0x05, layer = afm layer byte.
#   * Setup messages use fixed layer 0x00 (bases 0x02 and 0x0F).
#   * Front-panel button messages use base 0x0D, layer 0x00.
#
# Every encoder below returns a LIST of 9-byte message tuples (some params
# emit two messages: the filter params emit an AFM and an AWM variant, in
# that order, exactly as the VST does).
#
# NO midicrt / mido imports — pure data + pure functions.

from functools import partial

DEVICE_ID = "tg77"
DEVICE_NAME = "TG77"
DEFAULT_CHANNEL = 6   # rack instruments list: "Yamaha 1" = ch 6 — GUESS, verify on hardware
DEFAULT_DEVICE_NUMBER = 11  # VST's deviceNumber default choice index

_MANUFACTURER_ID = 0x43
_PARAMETER_CHANGE_ID = 0x34

# Cancel-burst behavior from the VST (host-side timing concern; data only):
CANCEL_BURST_DELAY_SECONDS = 0.035
CANCEL_BURST_REPEATS_PER_MESSAGE = 3


# ---------------------------------------------------------------------------
# Spec tables (verbatim from TG77ParameterDefinitions.h)
# ---------------------------------------------------------------------------

# (tab_name, base_address, pitch_eg_off_value, pitch_eg_on_value)
OPERATOR_INFOS = [
    ("OP1", 0x56, 0x09, 0x0B),
    ("OP2", 0x46, 0x1D, 0x1F),
    ("OP3", 0x36, 0x08, 0x0A),
    ("OP4", 0x26, 0x08, 0x0A),
    ("OP5", 0x16, 0x1C, 0x1E),
    ("OP6", 0x06, 0x09, 0x0B),
]

# Each spec: (id, label, min, max, default, choices_or_None)
OPERATOR_SPECS = [
    ("AfmEgHt", "AFM EG HT", 0, 63, 0, None),
    ("AfmEgR1", "AFM EG R1", 0, 63, 0, None),
    ("AfmEgR2", "AFM EG R2", 0, 63, 0, None),
    ("AfmEgR3", "AFM EG R3", 0, 63, 0, None),
    ("AfmEgR4", "AFM EG R4", 0, 63, 0, None),
    ("AfmEgRr1", "AFM EG RR1", 0, 63, 0, None),
    ("AfmEgRr2", "AFM EG RR2", 0, 63, 0, None),
    ("AfmEgL0", "AFM EG L0", 0, 63, 0, None),
    ("AfmEgL1", "AFM EG L1", 0, 63, 0, None),
    ("AfmEgL2", "AFM EG L2", 0, 63, 0, None),
    ("AfmEgL3", "AFM EG L3", 0, 63, 0, None),
    ("AfmEgL4", "AFM EG L4", 0, 63, 0, None),
    ("AfmEgRl1", "AFM EG RL1", 0, 63, 0, None),
    ("AfmEgRl2", "AFM EG RL2", 0, 63, 0, None),
    ("RateScalingSign", "Rate Scaling Sign", 0, 1, 1, ["+", "-"]),
    ("RateScalingValue", "Rate Scaling Value", 0, 7, 0, None),
    ("SusLoopPoint", "SusLoop Point", 1, 4, 1, None),
    ("OutputLevel", "Operator Output Level", 0, 127, 0, None),
    ("Bp1Amount", "BP1 Amount", 0, 255, 0, None),
    ("Bp2Amount", "BP2 Amount", 0, 255, 0, None),
    ("Bp3Amount", "BP3 Amount", 0, 255, 0, None),
    ("Bp4Amount", "BP4 Amount", 0, 255, 0, None),
    ("Bp1Keyboard", "BP1 Keyboard", 0, 127, 0, None),
    ("Bp2Keyboard", "BP2 Keyboard", 0, 127, 0, None),
    ("Bp3Keyboard", "BP3 Keyboard", 0, 127, 0, None),
    ("Bp4Keyboard", "BP4 Keyboard", 0, 127, 0, None),
    ("Mode", "AFM Mode", 0, 1, 0, ["Ratio", "Fixed"]),
    ("Coarse", "AFM Coarse", 0, 4, 0, ["<0.5", "I 1.0", "X 2.0", "C 3.0", "M 4.0"]),
    ("FineTuning", "AFM Fine Tuning", 0, 99, 0, None),
    ("DetuneValue", "AFM Detune Value", 0, 15, 0, None),
    ("DetuneSign", "AFM Detune Sign", 0, 1, 0, ["+", "-"]),
    ("Wave", "AFM Wave", 1, 16, 1, None),
    ("InitialPhase", "AFM Initial Phase", 0, 127, 0, None),
    ("PhaseSync", "AFM Phase Sync", 0, 1, 0, ["Off", "On"]),
    ("RateVel", "AFM Rate Vel", 0, 1, 0, ["Off", "On"]),
    ("AmpMod", "AFM Amp Mod", 0, 7, 0, None),
    ("PitchMod", "AFM Pitch Mod", 0, 7, 0, None),
    ("PitchEgSwitch", "AFM Pitch EG Switch", 0, 1, 0, ["Off", "On"]),
    ("KeyOnVelSign", "AFM Key On Vel Sign", 0, 1, 0, ["+", "-"]),
    ("KeyOnVel", "AFM Key On Vel", 0, 7, 0, None),
]

GLOBAL_SPECS = [
    ("mainAfmLfoFilterMod", "Main AFM LFO->Filter Mod", 0, 127, 0, None),
    ("afmMainLfoInitPhase", "AFM Main LFO InitPhase", 0, 99, 0, None),
    ("afmSubLfoPitchWave", "AFM Sub-LFO Pitch Wave", 0, 3, 0,
     ["Triangle", "Saw Down", "Square", "Sample&Hold"]),
    ("afmSubLfoPitchPitch", "AFM Sub-LFO Pitch", 0, 127, 0, None),
    ("afmSubLfoPitchSpeed", "AFM Sub-LFO Speed", 0, 127, 0, None),
    ("afmSubLfoPitchTime", "AFM Sub-LFO Time", 0, 99, 0, None),
    ("afmSubLfoPitchMode", "AFM Sub-LFO Mode", 0, 1, 0, ["Delay", "Decay"]),
]

FILTER_BANK_SPECS = [
    ("filterType", "Filter Type", 0, 2, 0, ["Thru", "LPF", "HPF"]),
    ("filterCutoff", "Filter Cutoff", 0, 127, 0, None),
    ("filterControlMode", "Filter Control Mode", 0, 2, 0, ["EG", "LFO", "EG-VA"]),
    ("filterEgR1", "Filter EG R1", 0, 63, 0, None),
    ("filterEgR2", "Filter EG R2", 0, 63, 0, None),
    ("filterEgR3", "Filter EG R3", 0, 63, 0, None),
    ("filterEgR4", "Filter EG R4", 0, 63, 0, None),
    ("filterEgRr1", "Filter EG RR1", 0, 63, 0, None),
    ("filterEgRr2", "Filter EG RR2", 0, 63, 0, None),
    ("filterEgL0", "Filter EG L0", -64, 63, 0, None),
    ("filterEgL1", "Filter EG L1", -64, 63, 0, None),
    ("filterEgL2", "Filter EG L2", -64, 63, 0, None),
    ("filterEgL3", "Filter EG L3", -64, 63, 0, None),
    ("filterEgL4", "Filter EG L4", -64, 63, 0, None),
    ("filterEgRl1", "Filter EG RL1", -64, 63, 0, None),
    ("filterEgRl2", "Filter EG RL2", -64, 63, 0, None),
    ("filterEgRateScalingSign", "Filter EG Rate Scaling Sign", 0, 1, 0, ["+", "-"]),
    ("filterEgRateScalingValue", "Filter EG Rate Scaling Value", 0, 7, 0, None),
    ("filterEgBp1Note", "Filter EG BP1 Note", 0, 127, 0, None),
    ("filterEgBp2Note", "Filter EG BP2 Note", 0, 127, 0, None),
    ("filterEgBp3Note", "Filter EG BP3 Note", 0, 127, 0, None),
    ("filterEgBp4Note", "Filter EG BP4 Note", 0, 127, 0, None),
    ("filterEgBp1Amount", "Filter EG BP1 Amount", 0, 255, 0, None),
    ("filterEgBp2Amount", "Filter EG BP2 Amount", 0, 255, 0, None),
    ("filterEgBp3Amount", "Filter EG BP3 Amount", 0, 255, 0, None),
    ("filterEgBp4Amount", "Filter EG BP4 Amount", 0, 255, 0, None),
]

FILTER_COMMON_SPECS = [
    ("filterResonance", "Filter Resonance", 0, 99, 0, None),
    ("filterEgKeyOnVelocitySign", "Filter EG Key On Velocity Sign", 0, 1, 0, ["+", "-"]),
    ("filterEgKeyOnVelocity", "Filter EG Key On Velocity", 0, 7, 0, None),
    ("filterLfoCutoffSensitivitySign", "Filter LFO Cutoff Sensitivity Sign", 0, 1, 0, ["+", "-"]),
    ("filterLfoCutoffSensitivity", "Filter LFO Cutoff Sensitivity", 0, 7, 0, None),
]

SETUP_SPECS = [
    ("setupFilterCutoffControlSource", "Filter Cutoff Control CC", 0, 121, 0, None),
    ("setupFilterCutoffControlAmount", "Filter Cutoff Control Amount", 0, 127, 0, None),
    ("setupBulkProtect", "Bulk Protect", 0, 1, 1, ["Off", "On"]),
]

AWM_SPECS = [
    ("awmWaveBank", "AWM Wave Bank", 0, 2, 0, ["Preset", "Card", "AFM"]),
    ("awmWave", "AWM Wave", 1, 256, 1, None),
    ("awmWaveFreqMode", "AWM Wave Freq Mode", 0, 1, 0, ["Normal", "Fixed"]),
    ("awmWaveFixedPitch", "AWM Fixed Pitch", 0, 59, 0, None),
    ("awmWaveFixedPitchFine", "AWM Fixed Pitch Fine", -64, 63, 0, None),
    ("awmAmpEgMode", "AWM Amp EG Mode", 0, 1, 0, ["Attack", "Hold"]),
    ("awmAmpEgHtR1", "AWM Amp EG HT/R1", 0, 63, 0, None),
    ("awmAmpEgR2", "AWM Amp EG R2", 0, 63, 0, None),
    ("awmAmpEgR3", "AWM Amp EG R3", 0, 63, 0, None),
    ("awmAmpEgR4", "AWM Amp EG R4", 0, 63, 0, None),
    ("awmAmpEgRr", "AWM Amp EG RR", 0, 63, 0, None),
    ("awmAmpEgL2", "AWM Amp EG L2", 0, 127, 0, None),
    ("awmAmpEgL3", "AWM Amp EG L3", 0, 127, 0, None),
    ("awmAmpEgRateScalingSign", "AWM Amp EG Rate Scaling Sign", 0, 1, 0, ["+", "-"]),
    ("awmAmpEgRateScalingValue", "AWM Amp EG Rate Scaling Value", 0, 7, 0, None),
    ("awmOutputLevelScalingBp1Note", "AWM Output Level BP1 Note", 0, 127, 0, None),
    ("awmOutputLevelScalingBp2Note", "AWM Output Level BP2 Note", 0, 127, 0, None),
    ("awmOutputLevelScalingBp3Note", "AWM Output Level BP3 Note", 0, 127, 0, None),
    ("awmOutputLevelScalingBp4Note", "AWM Output Level BP4 Note", 0, 127, 0, None),
    ("awmOutputLevelScalingBp1Amount", "AWM Output Level BP1 Amount", 0, 255, 0, None),
    ("awmOutputLevelScalingBp2Amount", "AWM Output Level BP2 Amount", 0, 255, 0, None),
    ("awmOutputLevelScalingBp3Amount", "AWM Output Level BP3 Amount", 0, 255, 0, None),
    ("awmOutputLevelScalingBp4Amount", "AWM Output Level BP4 Amount", 0, 255, 0, None),
    ("awmPitchEgR1", "AWM Pitch EG R1", 0, 63, 0, None),
    ("awmPitchEgR2", "AWM Pitch EG R2", 0, 63, 0, None),
    ("awmPitchEgR3", "AWM Pitch EG R3", 0, 63, 0, None),
    ("awmPitchEgRr", "AWM Pitch EG RR", 0, 63, 0, None),
    ("awmPitchEgL0", "AWM Pitch EG L0", 0, 127, 0, None),
    ("awmPitchEgL1", "AWM Pitch EG L1", 0, 127, 0, None),
    ("awmPitchEgL2", "AWM Pitch EG L2", 0, 127, 0, None),
    ("awmPitchEgL3", "AWM Pitch EG L3", 0, 127, 0, None),
    ("awmPitchEgRl", "AWM Pitch EG RL", 0, 127, 0, None),
    ("awmPitchEgScaleRange", "AWM Pitch EG Scale Range", 0, 3, 0,
     ["8 oct", "2 oct", "1 oct", "1/2 oct"]),
    ("awmPitchEgRateScalingSign", "AWM Pitch EG Rate Scaling Sign", 0, 1, 0, ["+", "-"]),
    ("awmPitchEgRateScalingValue", "AWM Pitch EG Rate Scaling Value", 0, 7, 0, None),
    ("awmPitchEgVelocity", "AWM Pitch EG Velocity", 0, 1, 0, ["Off", "On"]),
    ("awmKeyOnVelocitySign", "AWM Key On Velocity Sign", 0, 1, 0, ["+", "-"]),
    ("awmKeyOnVelocity", "AWM Key On Velocity", 0, 7, 0, None),
    ("awmRateVel", "AWM Rate Vel", 0, 1, 0, ["Off", "On"]),
    ("awmAmpModSign", "AWM Amp Mod Sign", 0, 1, 0, ["+", "-"]),
    ("awmAmpMod", "AWM Amp Mod", 0, 7, 0, None),
    ("awmPitchMod", "AWM Pitch Mod", 0, 7, 0, None),
    ("awmMainLfoSpeed", "AWM Main LFO Speed", 0, 127, 0, None),
    ("awmMainLfoDelayTime", "AWM Main LFO Delay Time", 0, 99, 0, None),
    ("awmMainLfoPitchModDepth", "AWM Main LFO Pitch Mod Depth", 0, 127, 0, None),
    ("awmMainLfoAmpModDepth", "AWM Main LFO Amp Mod Depth", 0, 127, 0, None),
    ("awmMainLfoFilterModDepth", "AWM Main LFO Filter Mod Depth", 0, 127, 0, None),
    ("awmMainLfoWave", "AWM Main LFO Wave", 0, 5, 0,
     ["Triangle", "Saw Down", "Saw Up", "Square", "Sine", "Sample&Hold"]),
    ("awmMainLfoInitialPhase", "AWM Main LFO Initial Phase", 0, 99, 0, None),
]


# ---------------------------------------------------------------------------
# Frame builder + layer helpers
# ---------------------------------------------------------------------------

def message(base, layer, address, msb, lsb, device_number=DEFAULT_DEVICE_NUMBER):
    """One TG77 parameter-change message as a 9-byte tuple (mido-style sysex
    data, i.e. everything between F0 and F7).  Mirrors the VST's
    addLiteralMessage byte-for-byte:
        43 (10|dev) 34 <base> <layer> 00 <address> <msb> <lsb>
    All payload bytes are masked to uint8 exactly like the C++ casts."""
    return (
        _MANUFACTURER_ID,
        0x10 | (device_number & 0x0F),
        _PARAMETER_CHANGE_ID,
        base & 0xFF,
        layer & 0xFF,
        0x00,
        address & 0xFF,
        msb & 0xFF,
        lsb & 0xFF,
    )


def afm_layer_byte(element_slot):
    """getAfmLayerByte(): element slot 0-3 -> 0x00/0x20/0x40/0x60."""
    return element_slot * 0x20


def slot_filter_base_byte(element_slot):
    """getSlotFilterBaseByte(): identical formula in the VST."""
    return element_slot * 0x20


# ---------------------------------------------------------------------------
# Generic per-family encoders.  Every encoder returns a list of message
# tuples.  Context kwargs: element_slot 0-3, filter_select 0-1,
# device_number 0-15.
# ---------------------------------------------------------------------------

# -- Global (base 0x05, layer = afm layer byte) -----------------------------

def _global_direct(address, value, element_slot=0, device_number=DEFAULT_DEVICE_NUMBER):
    return [message(0x05, afm_layer_byte(element_slot), address, 0x00, value,
                    device_number)]


# -- AFM operator (base = per-operator base address, layer = afm layer) -----

def _op_base(op):
    return OPERATOR_INFOS[op][1]


def _op_msg(op, address, msb, lsb, element_slot, device_number):
    return message(_op_base(op), afm_layer_byte(element_slot), address, msb, lsb,
                   device_number)


def _op_direct(address, op, value, element_slot=0, device_number=DEFAULT_DEVICE_NUMBER):
    return [_op_msg(op, address, 0x00, value, element_slot, device_number)]


def _op_inverted63(address, op, value, element_slot=0, device_number=DEFAULT_DEVICE_NUMBER):
    """AFM EG rates/levels: device wants 63 - value."""
    return [_op_msg(op, address, 0x00, 63 - value, element_slot, device_number)]


def _op_inverted255(address, op, value, element_slot=0, device_number=DEFAULT_DEVICE_NUMBER):
    """Operator breakpoint amounts: 255 - value, split msb=v//128, lsb=v%128."""
    inv = 255 - value
    return [_op_msg(op, address, inv // 128, inv % 128, element_slot, device_number)]


def op_pitch_eg_switch(op, pitch_eg_switch, element_slot=0,
                       device_number=DEFAULT_DEVICE_NUMBER):
    """Address 0x18: the VST sends a whole-byte LITERAL from OPERATOR_INFOS —
    pitch_eg_off_value when Off (0), pitch_eg_on_value when On (nonzero).
    NOTE: address 0x18 is shared with op_mode_pitch (mode + pitch-mod packing);
    these literals bake the operator's expected mode/pitch-mod state in."""
    info = OPERATOR_INFOS[op]
    literal = info[2] if pitch_eg_switch == 0 else info[3]
    return [_op_msg(op, 0x18, 0x00, literal, element_slot, device_number)]


def op_rate_scaling(op, sign, value, element_slot=0,
                    device_number=DEFAULT_DEVICE_NUMBER):
    """Packed: (sign * 8) + (7 - value) at address 0x0F.  Note the 7-value
    inversion — unique to the operator rate scaling (filter/AWM variants
    do NOT invert)."""
    packed = (sign * 8) + (7 - value)
    return [_op_msg(op, 0x0F, 0x00, packed, element_slot, device_number)]


def op_sus_loop_point(op, value, element_slot=0, device_number=DEFAULT_DEVICE_NUMBER):
    """1-4 in the UI, 0-3 on the wire (value - 1), address 0x0C."""
    return [_op_msg(op, 0x0C, 0x00, value - 1, element_slot, device_number)]


def op_output_level(op, value, element_slot=0, device_number=DEFAULT_DEVICE_NUMBER):
    """127 - value at address 0x1B."""
    return [_op_msg(op, 0x1B, 0x00, 127 - value, element_slot, device_number)]


def op_mode_pitch(op, mode, pitch_mod, element_slot=0,
                  device_number=DEFAULT_DEVICE_NUMBER):
    """Packed: (pitch_mod * 4) + 2 + mode at address 0x18 (shared with
    op_pitch_eg_switch's literal writes)."""
    packed = (pitch_mod * 4) + 2 + mode
    return [_op_msg(op, 0x18, 0x00, packed, element_slot, device_number)]


def op_detune(op, sign, value, element_slot=0, device_number=DEFAULT_DEVICE_NUMBER):
    """Packed: (0x00 if sign '+' else 0x10) + value at address 0x1A."""
    packed = (0x00 if sign == 0 else 0x10) + value
    return [_op_msg(op, 0x1A, 0x00, packed, element_slot, device_number)]


def op_wave(op, value, element_slot=0, device_number=DEFAULT_DEVICE_NUMBER):
    """1-16 in the UI, 0-15 on the wire (value - 1), address 0x17."""
    return [_op_msg(op, 0x17, 0x00, value - 1, element_slot, device_number)]


def op_phase(op, phase_sync, initial_phase, element_slot=0,
             device_number=DEFAULT_DEVICE_NUMBER):
    """Packed across the two data bytes at address 0x19:
    msb = 0x00 (sync Off) / 0x01 (sync On), lsb = initial phase."""
    msb = 0x00 if phase_sync == 0 else 0x01
    return [_op_msg(op, 0x19, msb, initial_phase, element_slot, device_number)]


def op_key_on_vel(op, sign, value, element_slot=0,
                  device_number=DEFAULT_DEVICE_NUMBER):
    """Packed: (0x00 if sign '+' else 0x08) + value at address 0x11."""
    packed = (0x00 if sign == 0 else 0x08) + value
    return [_op_msg(op, 0x11, 0x00, packed, element_slot, device_number)]


# -- Filter (base 0x09) — every param emits AFM then AWM variant ------------

def _filter_pair(address, msb, lsb, element_slot, filter_select, device_number):
    """AFM message (layer = slot_base + filter_select) followed by the AWM
    message (layer = slot_base + filter_select + 3), like the VST."""
    slot_base = slot_filter_base_byte(element_slot)
    return [
        message(0x09, slot_base + filter_select, address, msb, lsb, device_number),
        message(0x09, slot_base + filter_select + 3, address, msb, lsb, device_number),
    ]


def _filter_direct(address, value, element_slot=0, filter_select=0,
                   device_number=DEFAULT_DEVICE_NUMBER):
    return _filter_pair(address, 0x00, value, element_slot, filter_select,
                        device_number)


def _filter_signed7(address, value, element_slot=0, filter_select=0,
                    device_number=DEFAULT_DEVICE_NUMBER):
    """Filter EG levels -64..63 encoded as value + 64 (0..127)."""
    return _filter_pair(address, 0x00, value + 64, element_slot, filter_select,
                        device_number)


def _filter_two_byte(address, value, element_slot=0, filter_select=0,
                     device_number=DEFAULT_DEVICE_NUMBER):
    """Filter EG breakpoint amounts 0..255: msb=v//128, lsb=v%128 (NOT
    inverted, unlike the operator breakpoint amounts)."""
    return _filter_pair(address, value // 128, value % 128, element_slot,
                        filter_select, device_number)


def filter_eg_rate_scaling(sign, value, element_slot=0, filter_select=0,
                           device_number=DEFAULT_DEVICE_NUMBER):
    """Packed: value + sign * 8 at address 0x10 (NO 7-value inversion)."""
    return _filter_pair(0x10, 0x00, value + sign * 8, element_slot,
                        filter_select, device_number)


def _common_filter_pair(address, value, element_slot, device_number):
    """Common filter params: AFM common (offset 0x02) then AWM common
    (offset 0x05).  filter_select does not apply."""
    slot_base = slot_filter_base_byte(element_slot)
    return [
        message(0x09, slot_base + 0x02, address, 0x00, value, device_number),
        message(0x09, slot_base + 0x05, address, 0x00, value, device_number),
    ]


def filter_resonance(value, element_slot=0, device_number=DEFAULT_DEVICE_NUMBER):
    return _common_filter_pair(0x32, value, element_slot, device_number)


def filter_eg_key_on_velocity(sign, value, element_slot=0,
                              device_number=DEFAULT_DEVICE_NUMBER):
    """Packed: value + sign * 8 at common address 0x33."""
    return _common_filter_pair(0x33, value + sign * 8, element_slot, device_number)


def filter_lfo_cutoff_sensitivity(sign, value, element_slot=0,
                                  device_number=DEFAULT_DEVICE_NUMBER):
    """Packed: value + sign * 8 at common address 0x34."""
    return _common_filter_pair(0x34, value + sign * 8, element_slot, device_number)


# -- Setup (fixed layer 0x00) -----------------------------------------------

def setup_filter_cutoff_control_source(value, device_number=DEFAULT_DEVICE_NUMBER):
    return [message(0x02, 0x00, 0x32, 0x00, value, device_number)]


def setup_filter_cutoff_control_amount(value, device_number=DEFAULT_DEVICE_NUMBER):
    return [message(0x02, 0x00, 0x33, 0x00, value, device_number)]


def setup_bulk_protect(value, device_number=DEFAULT_DEVICE_NUMBER):
    return [message(0x0F, 0x00, 0x34, 0x00, value, device_number)]


# -- AWM element (base 0x07, layer = afm layer byte) ------------------------

def _awm_msg(address, msb, lsb, element_slot, device_number):
    return message(0x07, afm_layer_byte(element_slot), address, msb, lsb,
                   device_number)


def _awm_direct(address, value, element_slot=0, device_number=DEFAULT_DEVICE_NUMBER):
    return [_awm_msg(address, 0x00, value, element_slot, device_number)]


def _awm_inverted63(address, value, element_slot=0,
                    device_number=DEFAULT_DEVICE_NUMBER):
    """AWM Amp EG rates: 63 - value."""
    return [_awm_msg(address, 0x00, 63 - value, element_slot, device_number)]


def _awm_two_byte(address, value, element_slot=0,
                  device_number=DEFAULT_DEVICE_NUMBER):
    """AWM output-level breakpoint amounts 0..255: msb=v//128, lsb=v%128."""
    return [_awm_msg(address, value // 128, value % 128, element_slot,
                     device_number)]


def awm_wave(value, element_slot=0, device_number=DEFAULT_DEVICE_NUMBER):
    """1-256 in the UI; wire = value - 1 split across msb/lsb, address 0x01."""
    wire = value - 1
    return [_awm_msg(0x01, wire // 128, wire % 128, element_slot, device_number)]


def awm_wave_fixed_pitch_fine(value, element_slot=0,
                              device_number=DEFAULT_DEVICE_NUMBER):
    """-64..63 encoded as value + 64, address 0x04."""
    return [_awm_msg(0x04, 0x00, value + 64, element_slot, device_number)]


def awm_pitch_eg_rate_scaling(sign, value, element_slot=0,
                              device_number=DEFAULT_DEVICE_NUMBER):
    """Packed: sign * 8 + value at address 0x10 (no inversion)."""
    return [_awm_msg(0x10, 0x00, sign * 8 + value, element_slot, device_number)]


def awm_pitch_eg_velocity(value, device_number=DEFAULT_DEVICE_NUMBER):
    """Address 0x11 with LITERAL layer 0x00 — the VST does NOT use the
    element layer byte for this one parameter (tg77processor.cpp line 958)."""
    return [message(0x07, 0x00, 0x11, 0x00, value, device_number)]


def awm_main_lfo_wave(value, element_slot=0, device_number=DEFAULT_DEVICE_NUMBER):
    """Address 0x17.  Wire value skips 4: choices 0-3 map straight through,
    choices 4 (Sine) and 5 (Sample&Hold) map to 5 and 6."""
    wire = value + 1 if value >= 4 else value
    return [_awm_msg(0x17, 0x00, wire, element_slot, device_number)]


def awm_amp_eg_rate_scaling(sign, value, element_slot=0,
                            device_number=DEFAULT_DEVICE_NUMBER):
    """Packed: sign * 8 + value at address 0x57 (no inversion)."""
    return [_awm_msg(0x57, 0x00, sign * 8 + value, element_slot, device_number)]


def awm_key_on_velocity(sign, value, element_slot=0,
                        device_number=DEFAULT_DEVICE_NUMBER):
    """Packed: sign * 8 + value at address 0x60."""
    return [_awm_msg(0x60, 0x00, sign * 8 + value, element_slot, device_number)]


def awm_amp_mod(sign, value, element_slot=0, device_number=DEFAULT_DEVICE_NUMBER):
    """Packed: sign * 8 + value at address 0x62."""
    return [_awm_msg(0x62, 0x00, sign * 8 + value, element_slot, device_number)]


# -- Front-panel buttons (base 0x0D, layer 0x00) ----------------------------

def panel_cancel(device_number=DEFAULT_DEVICE_NUMBER):
    """Front-panel Cancel button: 0D 00 / addr 0x16 / 00 40."""
    return [message(0x0D, 0x00, 0x16, 0x00, 0x40, device_number)]


def panel_exit(device_number=DEFAULT_DEVICE_NUMBER):
    """Front-panel Exit button: 0D 00 / addr 0x18 / 00 40."""
    return [message(0x0D, 0x00, 0x18, 0x00, 0x40, device_number)]


def panel_cancel_burst(device_number=DEFAULT_DEVICE_NUMBER):
    """The VST's cancelBurst: 3x Cancel then 3x Exit, each spaced
    CANCEL_BURST_DELAY_SECONDS apart (timing is the caller's job — this
    returns the 6 messages in order)."""
    return (panel_cancel(device_number) * CANCEL_BURST_REPEATS_PER_MESSAGE
            + panel_exit(device_number) * CANCEL_BURST_REPEATS_PER_MESSAGE)


# ---------------------------------------------------------------------------
# SENDERS — spec id -> encoder callable.
#
# Conventions:
#   * Operator spec ids take (op, value..., element_slot=0, device_number=...).
#   * Filter-bank spec ids take (value..., element_slot=0, filter_select=0,
#     device_number=...) and return [AFM msg, AWM msg].
#   * Filter-common / setup / global / AWM ids take (value...,
#     element_slot=..., device_number=...) as documented per function.
#   * PACKED fields: all involved spec ids map to the SAME callable, which
#     takes every involved value (sign+value, mode+pitch_mod, sync+phase).
# ---------------------------------------------------------------------------

SENDERS = {
    # --- AFM operator: pitch EG switch literal ---
    "PitchEgSwitch": op_pitch_eg_switch,

    # --- AFM operator: inverted 63 - v EG rates/levels ---
    "AfmEgHt": partial(_op_inverted63, 0x0D),
    "AfmEgR1": partial(_op_inverted63, 0x00),
    "AfmEgR2": partial(_op_inverted63, 0x01),
    "AfmEgR3": partial(_op_inverted63, 0x02),
    "AfmEgR4": partial(_op_inverted63, 0x03),
    "AfmEgRr1": partial(_op_inverted63, 0x04),
    "AfmEgRr2": partial(_op_inverted63, 0x05),
    "AfmEgL0": partial(_op_inverted63, 0x0E),
    "AfmEgL1": partial(_op_inverted63, 0x06),
    "AfmEgL2": partial(_op_inverted63, 0x07),
    "AfmEgL3": partial(_op_inverted63, 0x08),
    "AfmEgL4": partial(_op_inverted63, 0x09),
    "AfmEgRl1": partial(_op_inverted63, 0x0A),
    "AfmEgRl2": partial(_op_inverted63, 0x0B),

    # --- AFM operator: packed / transformed ---
    "RateScalingSign": op_rate_scaling,     # fn(op, sign, value, ...)
    "RateScalingValue": op_rate_scaling,
    "SusLoopPoint": op_sus_loop_point,      # v - 1
    "OutputLevel": op_output_level,         # 127 - v
    "Mode": op_mode_pitch,                  # fn(op, mode, pitch_mod, ...)
    "PitchMod": op_mode_pitch,
    "DetuneSign": op_detune,                # fn(op, sign, value, ...)
    "DetuneValue": op_detune,
    "Wave": op_wave,                        # v - 1
    "PhaseSync": op_phase,                  # fn(op, phase_sync, initial_phase, ...)
    "InitialPhase": op_phase,
    "KeyOnVelSign": op_key_on_vel,          # fn(op, sign, value, ...)
    "KeyOnVel": op_key_on_vel,

    # --- AFM operator: inverted 255 - v two-byte breakpoint amounts ---
    "Bp1Amount": partial(_op_inverted255, 0x20),
    "Bp2Amount": partial(_op_inverted255, 0x21),
    "Bp3Amount": partial(_op_inverted255, 0x22),
    "Bp4Amount": partial(_op_inverted255, 0x23),

    # --- AFM operator: direct ---
    "Bp1Keyboard": partial(_op_direct, 0x1C),
    "Bp2Keyboard": partial(_op_direct, 0x1D),
    "Bp3Keyboard": partial(_op_direct, 0x1E),
    "Bp4Keyboard": partial(_op_direct, 0x1F),
    "Coarse": partial(_op_direct, 0x25),
    "FineTuning": partial(_op_direct, 0x26),
    "RateVel": partial(_op_direct, 0x24),
    "AmpMod": partial(_op_direct, 0x10),

    # --- Global (base 0x05, layer = element slot * 0x20) ---
    "mainAfmLfoFilterMod": partial(_global_direct, 0x11),
    "afmMainLfoInitPhase": partial(_global_direct, 0x13),
    "afmSubLfoPitchWave": partial(_global_direct, 0x15),
    "afmSubLfoPitchPitch": partial(_global_direct, 0x19),
    "afmSubLfoPitchSpeed": partial(_global_direct, 0x16),
    "afmSubLfoPitchTime": partial(_global_direct, 0x18),
    "afmSubLfoPitchMode": partial(_global_direct, 0x17),

    # --- Filter bank (base 0x09, AFM+AWM dual emit) ---
    "filterType": partial(_filter_direct, 0x00),
    "filterCutoff": partial(_filter_direct, 0x01),
    "filterControlMode": partial(_filter_direct, 0x02),
    "filterEgR1": partial(_filter_direct, 0x03),
    "filterEgR2": partial(_filter_direct, 0x04),
    "filterEgR3": partial(_filter_direct, 0x05),
    "filterEgR4": partial(_filter_direct, 0x06),
    "filterEgRr1": partial(_filter_direct, 0x07),
    "filterEgRr2": partial(_filter_direct, 0x08),
    "filterEgL0": partial(_filter_signed7, 0x09),
    "filterEgL1": partial(_filter_signed7, 0x0A),
    "filterEgL2": partial(_filter_signed7, 0x0B),
    "filterEgL3": partial(_filter_signed7, 0x0C),
    "filterEgL4": partial(_filter_signed7, 0x0D),
    "filterEgRl1": partial(_filter_signed7, 0x0E),
    "filterEgRl2": partial(_filter_signed7, 0x0F),
    "filterEgRateScalingSign": filter_eg_rate_scaling,   # fn(sign, value, ...)
    "filterEgRateScalingValue": filter_eg_rate_scaling,
    "filterEgBp1Note": partial(_filter_direct, 0x11),
    "filterEgBp2Note": partial(_filter_direct, 0x12),
    "filterEgBp3Note": partial(_filter_direct, 0x13),
    "filterEgBp4Note": partial(_filter_direct, 0x14),
    "filterEgBp1Amount": partial(_filter_two_byte, 0x15),
    "filterEgBp2Amount": partial(_filter_two_byte, 0x16),
    "filterEgBp3Amount": partial(_filter_two_byte, 0x17),
    "filterEgBp4Amount": partial(_filter_two_byte, 0x18),

    # --- Filter common (offsets 0x02 AFM / 0x05 AWM) ---
    "filterResonance": filter_resonance,
    "filterEgKeyOnVelocitySign": filter_eg_key_on_velocity,   # fn(sign, value, ...)
    "filterEgKeyOnVelocity": filter_eg_key_on_velocity,
    "filterLfoCutoffSensitivitySign": filter_lfo_cutoff_sensitivity,
    "filterLfoCutoffSensitivity": filter_lfo_cutoff_sensitivity,

    # --- Setup (fixed layer 0x00) ---
    "setupFilterCutoffControlSource": setup_filter_cutoff_control_source,
    "setupFilterCutoffControlAmount": setup_filter_cutoff_control_amount,
    "setupBulkProtect": setup_bulk_protect,

    # --- AWM (base 0x07) ---
    "awmWaveBank": partial(_awm_direct, 0x00),
    "awmWave": awm_wave,                          # (v-1) two-byte
    "awmWaveFreqMode": partial(_awm_direct, 0x02),
    "awmWaveFixedPitch": partial(_awm_direct, 0x03),
    "awmWaveFixedPitchFine": awm_wave_fixed_pitch_fine,   # v + 64
    "awmPitchMod": partial(_awm_direct, 0x05),
    "awmPitchEgR1": partial(_awm_direct, 0x06),
    "awmPitchEgR2": partial(_awm_direct, 0x07),
    "awmPitchEgR3": partial(_awm_direct, 0x08),
    "awmPitchEgRr": partial(_awm_direct, 0x09),
    "awmPitchEgL0": partial(_awm_direct, 0x0A),
    "awmPitchEgL1": partial(_awm_direct, 0x0B),
    "awmPitchEgL2": partial(_awm_direct, 0x0C),
    "awmPitchEgL3": partial(_awm_direct, 0x0D),
    "awmPitchEgRl": partial(_awm_direct, 0x0E),
    "awmPitchEgScaleRange": partial(_awm_direct, 0x0F),
    "awmPitchEgRateScalingSign": awm_pitch_eg_rate_scaling,   # fn(sign, value, ...)
    "awmPitchEgRateScalingValue": awm_pitch_eg_rate_scaling,
    "awmPitchEgVelocity": awm_pitch_eg_velocity,  # literal layer 0x00
    "awmMainLfoSpeed": partial(_awm_direct, 0x12),
    "awmMainLfoDelayTime": partial(_awm_direct, 0x13),
    "awmMainLfoPitchModDepth": partial(_awm_direct, 0x14),
    "awmMainLfoAmpModDepth": partial(_awm_direct, 0x15),
    "awmMainLfoFilterModDepth": partial(_awm_direct, 0x16),
    "awmMainLfoWave": awm_main_lfo_wave,          # skips wire value 4
    "awmMainLfoInitialPhase": partial(_awm_direct, 0x18),
    "awmAmpEgMode": partial(_awm_direct, 0x4F),
    "awmAmpEgHtR1": partial(_awm_inverted63, 0x50),
    "awmAmpEgR2": partial(_awm_inverted63, 0x51),
    "awmAmpEgR3": partial(_awm_inverted63, 0x52),
    "awmAmpEgR4": partial(_awm_inverted63, 0x53),
    "awmAmpEgRr": partial(_awm_inverted63, 0x54),
    "awmAmpEgL2": partial(_awm_direct, 0x55),
    "awmAmpEgL3": partial(_awm_direct, 0x56),
    "awmAmpEgRateScalingSign": awm_amp_eg_rate_scaling,   # fn(sign, value, ...)
    "awmAmpEgRateScalingValue": awm_amp_eg_rate_scaling,
    "awmOutputLevelScalingBp1Note": partial(_awm_direct, 0x58),
    "awmOutputLevelScalingBp2Note": partial(_awm_direct, 0x59),
    "awmOutputLevelScalingBp3Note": partial(_awm_direct, 0x5A),
    "awmOutputLevelScalingBp4Note": partial(_awm_direct, 0x5B),
    "awmOutputLevelScalingBp1Amount": partial(_awm_two_byte, 0x5C),
    "awmOutputLevelScalingBp2Amount": partial(_awm_two_byte, 0x5D),
    "awmOutputLevelScalingBp3Amount": partial(_awm_two_byte, 0x5E),
    "awmOutputLevelScalingBp4Amount": partial(_awm_two_byte, 0x5F),
    "awmKeyOnVelocitySign": awm_key_on_velocity,   # fn(sign, value, ...)
    "awmKeyOnVelocity": awm_key_on_velocity,
    "awmRateVel": partial(_awm_direct, 0x61),
    "awmAmpModSign": awm_amp_mod,                  # fn(sign, value, ...)
    "awmAmpMod": awm_amp_mod,
}

# Front-panel actions (not parameter specs; kept out of SENDERS):
PANEL_ACTIONS = {
    "cancel": panel_cancel,
    "exit": panel_exit,
    "cancelBurst": panel_cancel_burst,
}


# ---------------------------------------------------------------------------
# Generic dispatch — shared by the CRT page and the web control surface.
# ---------------------------------------------------------------------------

import inspect as _inspect

_SENDER_IDS = {}
for _sid, _fn in SENDERS.items():
    _SENDER_IDS.setdefault(id(_fn), []).append(_sid)


def _arg_spec_id(arg_name, candidate_ids, spec_id):
    """Map a sender argument name to the sibling spec id that supplies it."""
    if arg_name == "sign":
        for c in candidate_ids:
            if c.lower().endswith("sign"):
                return c
        return spec_id
    if arg_name == "value":
        non_sign = [c for c in candidate_ids if not c.lower().endswith("sign")]
        return non_sign[0] if non_sign else spec_id
    want = arg_name.replace("_", "").lower()
    for c in candidate_ids:
        if c.lower().endswith(want):
            return c
    return spec_id


def build_messages(spec_id, get_value, op=None, element_slot=0,
                   filter_select=0, device_number=DEFAULT_DEVICE_NUMBER):
    """Build the message list for editing `spec_id`.

    get_value(sibling_spec_id) -> current int value (spec-range units); it is
    consulted for every value-carrying argument, including packed siblings.
    `op` is the 0-based operator index for OPERATOR_SPECS ids, else None.
    """
    fn = SENDERS[spec_id]
    siblings = _SENDER_IDS[id(fn)]
    ctx = {
        "op": op,
        "element_slot": element_slot,
        "filter_select": filter_select,
        "device_number": device_number,
    }
    kwargs = {}
    for name in _inspect.signature(fn).parameters:
        if name in ctx:
            if ctx[name] is None:
                raise ValueError(f"{spec_id} needs {name}")
            kwargs[name] = ctx[name]
        else:
            kwargs[name] = int(get_value(_arg_spec_id(name, siblings, spec_id)))
    return fn(**kwargs)
