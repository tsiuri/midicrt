"""Unit tests for plugins/knobctl_control.py — the BLE keyboard's control-mode
state machine, sticky channel and footer indicator (pure Python, no MIDI)."""
from __future__ import annotations

import pytest

from plugins.knobctl_control import KeyboardControl

LO, HI = 60, 72


def _handshake(kc, t0=0.0, step=0.2):
    """Perform the enter handshake: lo x3, hi x3, lo. Returns list of verdicts."""
    seq = [LO, LO, LO, HI, HI, HI, LO]
    out = []
    t = t0
    for n in seq:
        out.append(kc.note_on(n, t))
        kc.note_off(n, t + 0.05)
        t += step
    return out


def test_handshake_enters_control_mode():
    kc = KeyboardControl(lo=LO, hi=HI)
    kc.knob(127, 0.0)
    verdicts = _handshake(kc)
    assert verdicts[:-1] == ["forward"] * 6
    assert verdicts[-1] == "enter"
    assert kc.in_control


def test_handshake_requires_knob_at_max():
    kc = KeyboardControl(lo=LO, hi=HI)
    kc.knob(60, 0.0)
    verdicts = _handshake(kc)
    assert verdicts == ["forward"] * 7
    assert not kc.in_control


def test_stray_note_resets_sequence():
    kc = KeyboardControl(lo=LO, hi=HI)
    kc.knob(127, 0.0)
    kc.note_on(LO, 0.0); kc.note_off(LO, 0.05)
    kc.note_on(LO, 0.2); kc.note_off(LO, 0.25)
    assert kc.note_on(62, 0.4) == "forward"   # D breaks the pattern
    kc.note_off(62, 0.45)
    # remainder of what would have been a valid pattern
    for i, n in enumerate([LO, HI, HI, HI, LO]):
        assert kc.note_on(n, 0.6 + i * 0.2) == "forward"
        kc.note_off(n, 0.65 + i * 0.2)
    assert not kc.in_control


def test_window_expiry_resets_sequence():
    kc = KeyboardControl(lo=LO, hi=HI, window_s=5.0)
    kc.knob(127, 0.0)
    verdicts = _handshake(kc, t0=0.0, step=1.0)   # last tap lands at t=6.0 > window
    assert verdicts[-1] == "forward"
    assert not kc.in_control


def test_control_mode_keys_step_pages_and_swallow_the_rest():
    kc = KeyboardControl(lo=LO, hi=HI)
    kc.knob(127, 0.0)
    _handshake(kc)
    assert kc.note_on(LO, 10.0) == "prev"
    assert kc.note_off(LO, 10.1) == "swallow"
    assert kc.note_on(HI, 10.2) == "next"
    assert kc.note_off(HI, 10.3) == "swallow"
    assert kc.note_on(64, 10.4) == "swallow"
    assert kc.note_off(64, 10.5) == "swallow"


def test_chord_hold_exits_after_hold_time():
    kc = KeyboardControl(lo=LO, hi=HI, hold_s=1.0)
    kc.knob(127, 0.0)
    _handshake(kc)
    kc.note_on(LO, 20.0)
    kc.note_on(HI, 20.1)
    assert kc.tick(20.6) is False
    assert kc.in_control
    assert kc.tick(21.1) is True
    assert not kc.in_control


def test_chord_hold_cancelled_by_third_key():
    kc = KeyboardControl(lo=LO, hi=HI, hold_s=1.0)
    kc.knob(127, 0.0)
    _handshake(kc)
    kc.note_on(LO, 20.0)
    kc.note_on(HI, 20.1)
    kc.note_on(64, 20.2)
    assert kc.tick(21.5) is False
    assert kc.in_control


def test_chord_hold_does_nothing_outside_control_mode():
    kc = KeyboardControl(lo=LO, hi=HI, hold_s=1.0)
    kc.note_on(LO, 0.0)
    kc.note_on(HI, 0.1)
    assert kc.tick(2.0) is False
    assert not kc.in_control


def test_sticky_channel_follows_controller_pages_and_survives_neutral_pages():
    kc = KeyboardControl(lo=LO, hi=HI, default_channel=1)
    assert kc.target_channel() == 1
    kc.observe_page_channel(3)
    assert kc.target_channel() == 3
    kc.observe_page_channel(None)      # moved to a neutral page
    assert kc.target_channel() == 3
    assert kc.sticky_channel == 3
    kc.observe_page_channel(7)
    assert kc.target_channel() == 7


def test_sticky_channel_can_be_restored_from_config():
    kc = KeyboardControl(lo=LO, hi=HI, default_channel=1, sticky_channel=5)
    assert kc.target_channel() == 5


def test_indicator_offline():
    kc = KeyboardControl(lo=LO, hi=HI)
    assert kc.indicator(now=0.0, source=None) == ("BT off", False)


def test_indicator_flashes_on_any_message_then_clears():
    kc = KeyboardControl(lo=LO, hi=HI, flash_s=0.12, default_channel=1)
    kc.touch(1.0)
    assert kc.indicator(now=1.05, source="BT") == ("BT ch1 *", False)
    assert kc.indicator(now=1.30, source="BT") == ("BT ch1  ", False)


def test_indicator_shows_source_and_sticky_channel():
    kc = KeyboardControl(lo=LO, hi=HI)
    kc.observe_page_channel(4)
    assert kc.indicator(now=0.0, source="USB") == ("USB ch4  ", False)


def test_indicator_in_control_mode_is_reverse():
    kc = KeyboardControl(lo=LO, hi=HI)
    kc.knob(127, 0.0)
    _handshake(kc)
    assert kc.indicator(now=5.0, source="BT") == (" BT CTRL ", True)


# ----- link state: the indicator must reflect the actual BLE connection -----

def test_indicator_disconnected_is_reverse_and_blinks():
    kc = KeyboardControl(lo=LO, hi=HI, offline_blink=True, blink_s=0.5)
    assert kc.indicator(now=0.1, source="BT", connected=False) == (" BT OFF ", True)
    assert kc.indicator(now=0.6, source="BT", connected=False) == (" BT OFF ", False)
    assert kc.indicator(now=1.1, source="BT", connected=False) == (" BT OFF ", True)


def test_indicator_disconnected_blink_can_be_disabled():
    kc = KeyboardControl(lo=LO, hi=HI, offline_blink=False, blink_s=0.5)
    assert kc.indicator(now=0.1, source="BT", connected=False) == (" BT OFF ", True)
    assert kc.indicator(now=0.6, source="BT", connected=False) == (" BT OFF ", True)


def test_indicator_disconnected_wins_over_control_mode():
    kc = KeyboardControl(lo=LO, hi=HI, offline_blink=False)
    kc.knob(127, 0.0)
    _handshake(kc)
    assert kc.indicator(now=5.0, source="BT", connected=False) == (" BT OFF ", True)


def test_indicator_connected_keeps_normal_text():
    kc = KeyboardControl(lo=LO, hi=HI)
    assert kc.indicator(now=0.0, source="BT", connected=True) == ("BT ch1  ", False)


# ----- tuned against the real SMK-25 capture of 2026-09-20 -----------------
# (knob = CC7 and rests around 119 after being "turned all the way up";
#  the player's two Cs are notes 48 and 60; a full combo takes ~1.6-2.1 s)

def test_fumbled_start_still_enters_on_the_last_seven_taps():
    kc = KeyboardControl(lo=LO, hi=HI)
    kc.knob(127, 0.0)
    t = 0.0
    verdicts = []
    for n in [LO, LO, LO, LO, HI, HI, HI, LO]:      # one low C too many
        verdicts.append(kc.note_on(n, t)); kc.note_off(n, t + 0.05); t += 0.2
    assert verdicts[-1] == "enter"
    assert kc.in_control


def test_real_captured_combo_enters_with_default_knob_threshold():
    # capture group at t=224.58: gaps 0.25 0.14 0.37 0.25 0.20 0.45, knob resting at 119
    kc = KeyboardControl(lo=48, hi=60)
    kc.knob(119, 0.0)
    times = [0.0, 0.25, 0.39, 0.76, 1.01, 1.21, 1.66]
    notes = [48, 48, 48, 60, 60, 60, 48]
    verdicts = []
    for t, n in zip(times, notes):
        verdicts.append(kc.note_on(n, t)); kc.note_off(n, t + 0.1)
    assert verdicts[-1] == "enter"


def test_knob_turned_down_before_the_last_tap_blocks_entry():
    kc = KeyboardControl(lo=LO, hi=HI)
    kc.knob(127, 0.0)
    t = 0.0
    for n in [LO, LO, LO, HI, HI, HI]:
        kc.note_on(n, t); kc.note_off(n, t + 0.05); t += 0.2
    kc.knob(10, t)
    assert kc.note_on(LO, t + 0.1) == "forward"
    assert not kc.in_control


def test_taps_before_leaving_control_mode_do_not_count_toward_reentry():
    kc = KeyboardControl(lo=LO, hi=HI, hold_s=1.0)
    kc.knob(127, 0.0)
    _handshake(kc)
    kc.note_on(LO, 10.0); kc.note_on(HI, 10.1)
    assert kc.tick(11.2) is True
    kc.note_off(LO, 11.3); kc.note_off(HI, 11.3)
    assert kc.note_on(LO, 11.5) == "forward"      # a single tap must not re-enter
    assert not kc.in_control
