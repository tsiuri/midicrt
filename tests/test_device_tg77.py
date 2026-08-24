# tests/test_device_tg77.py — byte-exact fidelity proof for devices/tg77.py
#
# Every expected tuple below was derived BY HAND from the authoritative C++
# (scratchpad tg77processor.cpp + TG77ParameterDefinitions.h), not from the
# Python module under test.  Frame body (mido sysex data, F0/F7 excluded):
#   43 (10|dev) 34 <base> <layer> 00 <address> <msb> <lsb>

from devices import tg77
from devices.tg77 import SENDERS


def test_frame_builder_matches_addLiteralMessage():
    # addLiteralMessage(base=0x05, layer=0x20, addr=0x11, msb=0x00, lsb=0x42)
    # with deviceNumber 3 -> F0 43 13 34 05 20 00 11 00 42 F7
    assert tg77.message(0x05, 0x20, 0x11, 0x00, 0x42, device_number=3) == (
        0x43, 0x13, 0x34, 0x05, 0x20, 0x00, 0x11, 0x00, 0x42)


def test_layer_byte_formulas():
    # getAfmLayerByte() / getSlotFilterBaseByte() = elementIndex * 0x20
    assert [tg77.afm_layer_byte(s) for s in range(4)] == [0x00, 0x20, 0x40, 0x60]
    assert [tg77.slot_filter_base_byte(s) for s in range(4)] == [0x00, 0x20, 0x40, 0x60]


def test_operator_direct_bp_keyboard():
    # sendDirect("Bp1Keyboard", 0x1C) on OP3 (base 0x36), element slot 1
    # (layer 0x20), device 0, value 69 -> lsb sent raw.
    assert SENDERS["Bp1Keyboard"](2, 69, element_slot=1, device_number=0) == [
        (0x43, 0x10, 0x34, 0x36, 0x20, 0x00, 0x1C, 0x00, 69)]


def test_operator_inverted63_eg_rate():
    # sendInverted63("AfmEgR1", 0x00): value 10 -> 63-10 = 53.  OP1 base 0x56,
    # slot 0, device 11 (VST default) -> status-ish byte 0x1B.
    assert SENDERS["AfmEgR1"](0, 10, element_slot=0, device_number=11) == [
        (0x43, 0x1B, 0x34, 0x56, 0x00, 0x00, 0x00, 0x00, 53)]
    # AfmEgHt goes to address 0x0D
    assert SENDERS["AfmEgHt"](0, 63, element_slot=0, device_number=11) == [
        (0x43, 0x1B, 0x34, 0x56, 0x00, 0x00, 0x0D, 0x00, 0)]


def test_operator_inverted255_breakpoint_amount():
    # sendInverted255("Bp1Amount", 0x20): value 100 -> 255-100 = 155
    # -> msb 155/128 = 1, lsb 155%128 = 27.  OP6 base 0x06, slot 3
    # (layer 0x60), device 5.
    assert SENDERS["Bp1Amount"](5, 100, element_slot=3, device_number=5) == [
        (0x43, 0x15, 0x34, 0x06, 0x60, 0x00, 0x20, 0x01, 0x1B)]


def test_operator_rate_scaling_packed_both_signs():
    # packed = (sign * 8) + (7 - amount) at addr 0x0F.  OP2 base 0x46.
    # sign '+' (0), value 3 -> 7-3 = 4
    assert SENDERS["RateScalingValue"](1, 0, 3, element_slot=0, device_number=0) == [
        (0x43, 0x10, 0x34, 0x46, 0x00, 0x00, 0x0F, 0x00, 4)]
    # sign '-' (1), value 3 -> 8 + 4 = 12
    assert SENDERS["RateScalingSign"](1, 1, 3, element_slot=0, device_number=0) == [
        (0x43, 0x10, 0x34, 0x46, 0x00, 0x00, 0x0F, 0x00, 12)]


def test_operator_detune_packed():
    # (sign == 0 ? 0x00 : 0x10) + value at addr 0x1A.  OP1 base 0x56.
    assert SENDERS["DetuneValue"](0, 0, 5, element_slot=0, device_number=0) == [
        (0x43, 0x10, 0x34, 0x56, 0x00, 0x00, 0x1A, 0x00, 0x05)]
    assert SENDERS["DetuneSign"](0, 1, 5, element_slot=0, device_number=0) == [
        (0x43, 0x10, 0x34, 0x56, 0x00, 0x00, 0x1A, 0x00, 0x15)]


def test_operator_key_on_vel_packed():
    # (sign == 0 ? 0x00 : 0x08) + value at addr 0x11.  OP4 base 0x26.
    assert SENDERS["KeyOnVel"](3, 1, 7, element_slot=0, device_number=0) == [
        (0x43, 0x10, 0x34, 0x26, 0x00, 0x00, 0x11, 0x00, 0x0F)]
    assert SENDERS["KeyOnVelSign"](3, 0, 7, element_slot=0, device_number=0) == [
        (0x43, 0x10, 0x34, 0x26, 0x00, 0x00, 0x11, 0x00, 0x07)]


def test_pitch_eg_switch_uses_operator_info_literals():
    # OP5: base 0x16, pitchEgOff 0x1C, pitchEgOn 0x1E.  Address 0x18, literal
    # whole-byte values from operatorInfos.
    assert SENDERS["PitchEgSwitch"](4, 0, element_slot=0, device_number=0) == [
        (0x43, 0x10, 0x34, 0x16, 0x00, 0x00, 0x18, 0x00, 0x1C)]
    assert SENDERS["PitchEgSwitch"](4, 1, element_slot=0, device_number=0) == [
        (0x43, 0x10, 0x34, 0x16, 0x00, 0x00, 0x18, 0x00, 0x1E)]
    # OP2 literals differ: off 0x1D, on 0x1F (base 0x46)
    assert SENDERS["PitchEgSwitch"](1, 1, element_slot=2, device_number=0) == [
        (0x43, 0x10, 0x34, 0x46, 0x40, 0x00, 0x18, 0x00, 0x1F)]


def test_operator_mode_pitch_packed_shares_addr_0x18():
    # (pitchMod * 4) + 2 + mode at addr 0x18.  OP1 base 0x56.
    # mode=1 (Fixed), pitchMod=3 -> 12 + 2 + 1 = 15
    assert SENDERS["Mode"](0, 1, 3, element_slot=0, device_number=0) == [
        (0x43, 0x10, 0x34, 0x56, 0x00, 0x00, 0x18, 0x00, 15)]


def test_operator_phase_packed_msb_lsb():
    # addr 0x19: msb = phaseSync (0/1), lsb = initialPhase.  OP1.
    assert SENDERS["PhaseSync"](0, 1, 100, element_slot=0, device_number=0) == [
        (0x43, 0x10, 0x34, 0x56, 0x00, 0x00, 0x19, 0x01, 100)]


def test_operator_output_level_and_sus_loop_and_wave():
    # OutputLevel: 127 - v at 0x1B; SusLoopPoint: v-1 at 0x0C; Wave: v-1 at 0x17
    assert SENDERS["OutputLevel"](0, 27, element_slot=0, device_number=0) == [
        (0x43, 0x10, 0x34, 0x56, 0x00, 0x00, 0x1B, 0x00, 100)]
    assert SENDERS["SusLoopPoint"](0, 4, element_slot=0, device_number=0) == [
        (0x43, 0x10, 0x34, 0x56, 0x00, 0x00, 0x0C, 0x00, 3)]
    assert SENDERS["Wave"](0, 16, element_slot=0, device_number=0) == [
        (0x43, 0x10, 0x34, 0x56, 0x00, 0x00, 0x17, 0x00, 15)]


def test_filter_dual_emit_with_slot_and_filter_offsets():
    # filterCutoff addr 0x01, element slot 2 (slot base 0x40), filter 2
    # (filter_select=1): AFM layer 0x40+1 = 0x41, AWM layer 0x40+1+3 = 0x44.
    # Base is always 0x09 for filter messages.
    assert SENDERS["filterCutoff"](100, element_slot=2, filter_select=1,
                                   device_number=0) == [
        (0x43, 0x10, 0x34, 0x09, 0x41, 0x00, 0x01, 0x00, 100),
        (0x43, 0x10, 0x34, 0x09, 0x44, 0x00, 0x01, 0x00, 100)]


def test_filter_signed7_negative_level():
    # filterEgL0 addr 0x09, value -30 -> -30 + 64 = 34.  Slot 0, filter 1:
    # layers 0x00 and 0x03.
    assert SENDERS["filterEgL0"](-30, element_slot=0, filter_select=0,
                                 device_number=0) == [
        (0x43, 0x10, 0x34, 0x09, 0x00, 0x00, 0x09, 0x00, 34),
        (0x43, 0x10, 0x34, 0x09, 0x03, 0x00, 0x09, 0x00, 34)]


def test_filter_two_byte_bp_amount_not_inverted():
    # filterEgBp1Amount addr 0x15, value 200 -> msb 1, lsb 72 (no inversion,
    # unlike operator Bp amounts).
    assert SENDERS["filterEgBp1Amount"](200, element_slot=0, filter_select=0,
                                        device_number=0) == [
        (0x43, 0x10, 0x34, 0x09, 0x00, 0x00, 0x15, 0x01, 72),
        (0x43, 0x10, 0x34, 0x09, 0x03, 0x00, 0x15, 0x01, 72)]


def test_filter_rate_scaling_packed_no_inversion():
    # value + sign*8 at addr 0x10 (contrast with operator's 7-v inversion).
    assert SENDERS["filterEgRateScalingValue"](1, 5, element_slot=0,
                                               filter_select=0,
                                               device_number=0) == [
        (0x43, 0x10, 0x34, 0x09, 0x00, 0x00, 0x10, 0x00, 13),
        (0x43, 0x10, 0x34, 0x09, 0x03, 0x00, 0x10, 0x00, 13)]


def test_filter_common_key_on_velocity():
    # Common filter: AFM offset 0x02, AWM offset 0x05 on top of slot base;
    # keyOnVelocity packed value + sign*8 at addr 0x33.  Slot 1 -> 0x22/0x25.
    assert SENDERS["filterEgKeyOnVelocity"](1, 2, element_slot=1,
                                            device_number=0) == [
        (0x43, 0x10, 0x34, 0x09, 0x22, 0x00, 0x33, 0x00, 10),
        (0x43, 0x10, 0x34, 0x09, 0x25, 0x00, 0x33, 0x00, 10)]


def test_global_param_uses_afm_layer():
    # mainAfmLfoFilterMod: base 0x05, addr 0x11, slot 3 layer 0x60.
    assert SENDERS["mainAfmLfoFilterMod"](99, element_slot=3,
                                          device_number=0) == [
        (0x43, 0x10, 0x34, 0x05, 0x60, 0x00, 0x11, 0x00, 99)]


def test_awm_direct_and_inverted():
    # awmMainLfoSpeed: base 0x07 addr 0x12 direct.  Slot 1 layer 0x20.
    assert SENDERS["awmMainLfoSpeed"](88, element_slot=1, device_number=0) == [
        (0x43, 0x10, 0x34, 0x07, 0x20, 0x00, 0x12, 0x00, 88)]
    # awmAmpEgR2: addr 0x51, inverted 63 - 20 = 43.
    assert SENDERS["awmAmpEgR2"](20, element_slot=0, device_number=0) == [
        (0x43, 0x10, 0x34, 0x07, 0x00, 0x00, 0x51, 0x00, 43)]


def test_awm_wave_two_byte_minus_one():
    # awmWave value 200 -> wire 199 -> msb 1, lsb 71 at addr 0x01.
    assert SENDERS["awmWave"](200, element_slot=0, device_number=0) == [
        (0x43, 0x10, 0x34, 0x07, 0x00, 0x00, 0x01, 0x01, 71)]


def test_awm_main_lfo_wave_skips_4():
    # Choice 3 (Square) -> wire 3; choice 4 (Sine) -> wire 5.
    assert SENDERS["awmMainLfoWave"](3, element_slot=0, device_number=0)[0][8] == 3
    assert SENDERS["awmMainLfoWave"](4, element_slot=0, device_number=0)[0][8] == 5
    assert SENDERS["awmMainLfoWave"](5, element_slot=0, device_number=0)[0][8] == 6


def test_awm_pitch_eg_velocity_literal_layer_zero():
    # The one AWM param the VST sends with layer literal 0x00 regardless of
    # element slot (tg77processor.cpp line 958).
    assert SENDERS["awmPitchEgVelocity"](1, device_number=0) == [
        (0x43, 0x10, 0x34, 0x07, 0x00, 0x00, 0x11, 0x00, 1)]


def test_awm_fixed_pitch_fine_signed7():
    # -10 -> 54 at addr 0x04.
    assert SENDERS["awmWaveFixedPitchFine"](-10, element_slot=0,
                                            device_number=0) == [
        (0x43, 0x10, 0x34, 0x07, 0x00, 0x00, 0x04, 0x00, 54)]


def test_awm_key_on_velocity_packed():
    # sign*8 + value at addr 0x60.
    assert SENDERS["awmKeyOnVelocity"](1, 6, element_slot=0, device_number=0) == [
        (0x43, 0x10, 0x34, 0x07, 0x00, 0x00, 0x60, 0x00, 14)]


def test_setup_and_bulk_protect():
    # setupFilterCutoffControlSource: base 0x02, layer 0x00, addr 0x32.
    assert SENDERS["setupFilterCutoffControlSource"](74, device_number=0) == [
        (0x43, 0x10, 0x34, 0x02, 0x00, 0x00, 0x32, 0x00, 74)]
    # setupBulkProtect: base 0x0F, layer 0x00, addr 0x34.
    assert SENDERS["setupBulkProtect"](1, device_number=0) == [
        (0x43, 0x10, 0x34, 0x0F, 0x00, 0x00, 0x34, 0x00, 1)]


def test_panel_buttons():
    # Cancel: 0D 00 / 16 / 00 40.  Exit: 0D 00 / 18 / 00 40.
    assert tg77.PANEL_ACTIONS["cancel"](device_number=0) == [
        (0x43, 0x10, 0x34, 0x0D, 0x00, 0x00, 0x16, 0x00, 0x40)]
    assert tg77.PANEL_ACTIONS["exit"](device_number=0) == [
        (0x43, 0x10, 0x34, 0x0D, 0x00, 0x00, 0x18, 0x00, 0x40)]
    burst = tg77.PANEL_ACTIONS["cancelBurst"](device_number=0)
    assert len(burst) == 6
    assert burst[:3] == tg77.PANEL_ACTIONS["cancel"](device_number=0) * 3
    assert burst[3:] == tg77.PANEL_ACTIONS["exit"](device_number=0) * 3


def test_every_spec_id_has_a_sender():
    spec_ids = [s[0] for s in (tg77.OPERATOR_SPECS + tg77.GLOBAL_SPECS
                               + tg77.FILTER_BANK_SPECS + tg77.FILTER_COMMON_SPECS
                               + tg77.SETUP_SPECS + tg77.AWM_SPECS)]
    missing = [sid for sid in spec_ids if sid not in SENDERS]
    assert missing == []


def test_all_messages_are_9_bytes_with_zero_at_index_5():
    samples = (
        SENDERS["AfmEgR1"](0, 0, element_slot=0, device_number=15)
        + SENDERS["filterCutoff"](0, element_slot=3, filter_select=1, device_number=15)
        + SENDERS["awmWave"](256, element_slot=3, device_number=15)
    )
    for msg in samples:
        assert len(msg) == 9
        assert msg[0] == 0x43 and msg[2] == 0x34 and msg[5] == 0x00
        assert msg[1] == 0x1F  # 0x10 | 15
