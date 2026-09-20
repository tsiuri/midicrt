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
    assert kc.note_on(61, 10.4) == "swallow"      # C#: not mapped to anything
    assert kc.note_off(61, 10.5) == "swallow"


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


def test_default_chord_hold_is_half_a_second():
    kc = KeyboardControl(lo=LO, hi=HI)          # no hold_s given
    kc.knob(127, 0.0)
    _handshake(kc)
    kc.note_on(LO, 20.0)
    kc.note_on(HI, 20.0)
    assert kc.tick(20.4) is False
    assert kc.tick(20.55) is True


# ----- reminder when the combo is right but the knob is not up ---------------

def test_correct_combo_with_low_knob_shows_reverse_reminder_for_one_second():
    kc = KeyboardControl(lo=LO, hi=HI, prefix_min=100)
    kc.knob(60, 0.0)
    verdicts = _handshake(kc, t0=1.0, step=0.2)      # last tap lands at t=2.2
    assert verdicts[-1] == "forward" and not kc.in_control
    assert kc.indicator(now=2.3, source="BT") == (" KNOB UNDER 100 ", True)
    assert kc.indicator(now=3.1, source="BT") == (" KNOB UNDER 100 ", True)
    assert kc.indicator(now=3.3, source="BT")[1] is False      # back to normal


def test_correct_combo_with_knob_never_seen_says_turn_knob_up():
    kc = KeyboardControl(lo=LO, hi=HI, prefix_min=100)   # no knob() call: position unknown
    _handshake(kc, t0=1.0, step=0.2)
    assert kc.indicator(now=2.3, source="BT") == (" TURN KNOB UP ", True)


def test_wrong_combo_shows_no_reminder():
    kc = KeyboardControl(lo=LO, hi=HI, prefix_min=100)
    kc.knob(60, 0.0)
    t = 1.0
    for n in [LO, LO, HI, HI, HI, LO, LO]:
        kc.note_on(n, t); kc.note_off(n, t + 0.05); t += 0.2
    assert kc.indicator(now=t, source="BT")[1] is False


# ----- in-page navigation keys (capture 2026-09-20: D E F G A B above lo-C) ----

def _enter(kc, lo, t0=0.0):
    kc.knob(127, t0)
    t = t0
    for n in [lo, lo, lo, lo + 12, lo + 12, lo + 12, lo]:
        v = kc.note_on(n, t); kc.note_off(n, t + 0.05); t += 0.2
    assert v == "enter"
    return t


NAV = {2: "KEY_UP", 4: "KEY_DOWN", 5: "KEY_LEFT", 7: "KEY_RIGHT", 9: "KEY_ENTER", 11: "KEY_ESCAPE"}


def test_navigation_keys_in_control_mode():
    kc = KeyboardControl(lo=48, hi=60)
    t = _enter(kc, 48)
    for off, name in NAV.items():
        assert kc.note_on(48 + off, t) == "key:" + name
        assert kc.note_off(48 + off, t + 0.1) == "swallow"
        t += 0.3


def test_navigation_keys_work_in_octaves_2_and_4_too():
    kc = KeyboardControl(lo=48, hi=60)
    t = _enter(kc, 48)
    for base in (36, 60):
        for off, name in NAV.items():
            assert kc.note_on(base + off, t) == "key:" + name
            kc.note_off(base + off, t + 0.1); t += 0.3


def test_navigation_keys_are_plain_notes_outside_control_mode():
    kc = KeyboardControl(lo=48, hi=60)
    assert kc.note_on(50, 0.0) == "forward"


def test_notes_outside_the_three_octaves_are_swallowed_in_control_mode():
    kc = KeyboardControl(lo=48, hi=60)
    t = _enter(kc, 48)
    assert kc.note_on(26, t) == "swallow"       # D1
    assert kc.note_on(86, t + 0.3) == "swallow"  # D6


def test_handshake_and_exit_chord_work_in_octave_2_and_octave_4():
    for lo in (36, 60):
        kc = KeyboardControl(lo=48, hi=60)
        t = _enter(kc, lo)
        kc.note_on(lo, t + 1.0); kc.note_on(lo + 12, t + 1.0)
        assert kc.tick(t + 1.6) is True
        assert not kc.in_control


def test_page_step_cs_are_relative_to_the_c_used_to_enter():
    kc = KeyboardControl(lo=48, hi=60)
    t = _enter(kc, 48)
    for note, want in ((36, "prev"), (48, "prev"), (60, "next"), (72, "next")):
        assert kc.note_on(note, t) == want
        kc.note_off(note, t + 0.05); t += 0.3


def test_held_arrow_repeats_after_delay_then_at_interval():
    kc = KeyboardControl(lo=48, hi=60, repeat_delay_s=0.4, repeat_interval_s=0.125)
    t = _enter(kc, 48)
    assert kc.note_on(50, t) == "key:KEY_UP"
    assert kc.poll_repeat(t + 0.30) == []
    assert kc.poll_repeat(t + 0.41) == ["KEY_UP"]
    assert kc.poll_repeat(t + 0.45) == []
    assert kc.poll_repeat(t + 0.54) == ["KEY_UP"]
    kc.note_off(50, t + 0.6)
    assert kc.poll_repeat(t + 1.0) == []


def test_enter_and_escape_never_repeat():
    kc = KeyboardControl(lo=48, hi=60)
    t = _enter(kc, 48)
    kc.note_on(57, t)
    assert kc.poll_repeat(t + 2.0) == []
    kc.note_off(57, t + 2.1)
    kc.note_on(59, t + 3.0)
    assert kc.poll_repeat(t + 5.0) == []


def test_another_key_cancels_a_running_repeat():
    kc = KeyboardControl(lo=48, hi=60)
    t = _enter(kc, 48)
    kc.note_on(50, t)
    kc.note_on(57, t + 0.1)          # enter pressed while up is held
    assert kc.poll_repeat(t + 1.0) == []


def test_custom_keymap_is_respected():
    kc = KeyboardControl(lo=48, hi=60, keymap={2: "KEY_DOWN"})
    t = _enter(kc, 48)
    assert kc.note_on(50, t) == "key:KEY_DOWN"
    assert kc.note_on(52, t + 0.3) == "swallow"


# ----- on-screen help text is generated from the live bindings ---------------

def test_help_lines_describe_the_default_bindings():
    kc = KeyboardControl(lo=48, hi=60)
    assert kc.help_lines() == [
        "BT KEYBOARD CONTROL",
        "enter kb control mode: turn wheel to max.",
        "press C4 3x, then C5 3x, then C4 1x more.",
        "D up  E down  F left  G right",
        "A enter  B esc/menu",
        "C4 prev page  C5 next page",
        "exit kb control mode: hold C4+C5 for 0.5s.",
        "also works one octave down / up",
    ]


def test_help_lines_follow_a_custom_keymap_and_hold_time():
    kc = KeyboardControl(lo=48, hi=60, keymap={2: "KEY_DOWN", 9: "KEY_ENTER"}, hold_s=1.0,
                         octaves=(0,))
    lines = kc.help_lines()
    assert "D down" in lines
    assert "A enter" in lines
    assert not any("left" in l or "right" in l for l in lines)
    assert lines[-1] == "exit kb control mode: hold C4+C5 for 1s."   # single octave: no "also works" line


def test_help_lines_fit_the_menu_panel():
    assert max(len(l) for l in KeyboardControl(lo=48, hi=60).help_lines()) <= 44
