# plugins/knobctl_control.py — pure-Python state machine behind plugins/knobctl.py
#
# Owns three things for the little BLE keyboard (SMK-25), with NO MIDI/mido
# dependency so it can be unit-tested (tests/test_knobctl_control.py):
#
#   * sticky target channel — the channel of the last controller page visited
#     keeps receiving the keyboard's notes even after moving to a neutral page.
#   * keyboard-control mode — entered by a handshake (knob at max, then
#     lo x3, hi x3, lo within a window); while active lo/hi step pages and every
#     other note is swallowed; exited by holding lo+hi together for hold_s.
#   * footer indicator — "BT ch3 *" style text with a short activity flash on
#     ANY received message, reverse-video " BT CTRL " while in control mode.
#
# Verdicts returned by note_on()/note_off():
#   "forward"  pass the note to the rack as usual
#   "swallow"  drop it (control mode)
#   "enter"    the tap that completed the handshake (drop it; mode is now on)
#   "prev"/"next"  page step request (control mode; drop the note)

from __future__ import annotations

HANDSHAKE = ("lo", "lo", "lo", "hi", "hi", "hi", "lo")


class KeyboardControl:
    def __init__(self, lo=60, hi=72, window_s=5.0, hold_s=1.0, prefix_min=126,
                 flash_s=0.12, sticky_channel=None, default_channel=1):
        self.lo = int(lo)
        self.hi = int(hi)
        self.window_s = float(window_s)
        self.hold_s = float(hold_s)
        self.prefix_min = int(prefix_min)
        self.flash_s = float(flash_s)
        self.default_channel = int(default_channel)
        self.sticky_channel = int(sticky_channel) if sticky_channel else None
        self.in_control = False
        self._knob_value = None
        self._seq_pos = 0          # how many handshake steps matched so far
        self._seq_t0 = None        # time of the first matched tap
        self._held = set()         # notes currently down (all modes)
        self._chord_since = None   # when lo+hi (and nothing else) became held
        self._last_msg_t = None

    # ----- inputs -------------------------------------------------------
    def touch(self, now):
        """Any message received (bound or not) — feeds the activity flash."""
        self._last_msg_t = now

    def knob(self, value, now):
        self.touch(now)
        self._knob_value = int(value)

    def observe_page_channel(self, channel):
        """Called with the current page's note_target_channel (or None)."""
        if channel:
            self.sticky_channel = max(1, min(16, int(channel)))

    def target_channel(self):
        return self.sticky_channel or self.default_channel

    def note_on(self, note, now):
        self.touch(now)
        self._held.add(note)
        self._update_chord(now)
        if self.in_control:
            if note == self.lo:
                return "prev"
            if note == self.hi:
                return "next"
            return "swallow"
        return self._advance_handshake(note, now)

    def note_off(self, note, now):
        self.touch(now)
        self._held.discard(note)
        self._update_chord(now)
        return "swallow" if self.in_control else "forward"

    def tick(self, now):
        """Poll from the worker loop. Returns True when the chord-hold just
        ended control mode."""
        if self.in_control and self._chord_since is not None \
                and now - self._chord_since >= self.hold_s:
            self.in_control = False
            self._chord_since = None
            return True
        return False

    # ----- indicator ----------------------------------------------------
    def indicator(self, now, source):
        """(text, reverse). source: 'BT' | 'USB' | None (offline)."""
        if source is None:
            return "BT off", False
        if self.in_control:
            return f" {source} CTRL ", True
        flashing = self._last_msg_t is not None and now - self._last_msg_t < self.flash_s
        dot = "*" if flashing else " "
        return f"{source} ch{self.target_channel()} {dot}", False

    # ----- internals ----------------------------------------------------
    def _update_chord(self, now):
        if self._held == {self.lo, self.hi}:
            if self._chord_since is None:
                self._chord_since = now
        else:
            self._chord_since = None

    def _advance_handshake(self, note, now):
        want = HANDSHAKE[self._seq_pos] if self._seq_pos < len(HANDSHAKE) else None
        got = "lo" if note == self.lo else "hi" if note == self.hi else None
        if self._seq_pos == 0:
            prefix_ok = self._knob_value is not None and self._knob_value >= self.prefix_min
            if got == "lo" and prefix_ok:
                self._seq_pos = 1
                self._seq_t0 = now
            return "forward"
        if now - self._seq_t0 > self.window_s or got != want:
            self._reset_seq()
            return "forward"
        self._seq_pos += 1
        if self._seq_pos == len(HANDSHAKE):
            self._reset_seq()
            self.in_control = True
            self._chord_since = None
            return "enter"
        return "forward"

    def _reset_seq(self):
        self._seq_pos = 0
        self._seq_t0 = None
