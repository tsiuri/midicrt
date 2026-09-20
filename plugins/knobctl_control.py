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
#     ANY received message, reverse-video " BT CTRL " while in control mode,
#     blinking reverse-video " BT OFF " while the keyboard's link is down.
#
# Verdicts returned by note_on()/note_off():
#   "forward"  pass the note to the rack as usual
#   "swallow"  drop it (control mode)
#   "enter"    the tap that completed the handshake (drop it; mode is now on)
#   "prev"/"next"  page step request (control mode; drop the note)

from __future__ import annotations

HANDSHAKE = ("lo", "lo", "lo", "hi", "hi", "hi", "lo")

# Navigation keys in control mode, as semitone offsets above a C — measured from
# the player (capture 2026-09-20): D E F G A B = up down left right enter back.
DEFAULT_KEYMAP = {2: "KEY_UP", 4: "KEY_DOWN", 5: "KEY_LEFT", 7: "KEY_RIGHT",
                  9: "KEY_ENTER", 11: "KEY_ESCAPE"}
REPEATING = {"KEY_UP", "KEY_DOWN", "KEY_LEFT", "KEY_RIGHT"}   # held arrows auto-repeat


class KeyboardControl:
    def __init__(self, lo=60, hi=72, window_s=5.0, hold_s=0.5, prefix_min=100,
                 flash_s=0.12, sticky_channel=None, default_channel=1,
                 offline_blink=True, blink_s=0.5, keymap=None, octaves=(-1, 0, 1),
                 repeat_delay_s=0.4, repeat_interval_s=0.125):
        self.lo = int(lo)
        self.hi = int(hi)
        self.window_s = float(window_s)
        self.hold_s = float(hold_s)
        self.prefix_min = int(prefix_min)
        self.flash_s = float(flash_s)
        self.offline_blink = bool(offline_blink)
        self.blink_s = float(blink_s)
        self.default_channel = int(default_channel)
        self.sticky_channel = int(sticky_channel) if sticky_channel else None
        self.in_control = False
        self._knob_value = None
        self._taps = []            # recent note-on taps: (note, time), newest last
        self._held = set()         # notes currently down (all modes)
        self._chord_since = None   # when lo+hi (and nothing else) became held
        self._last_msg_t = None
        self._knob_warn_t = None   # when a correct combo was refused for the knob
        self.warn_s = 1.0
        # The same controls work in every octave listed: lo/hi (and the keymap)
        # transposed by whole octaves. `base` = the lo-C actually used to enter.
        span = self.hi - self.lo
        self.bases = sorted(self.lo + span * int(o) for o in octaves)
        self._span = span
        self.base = self.lo
        self.keymap = {int(k): str(v) for k, v in (keymap or DEFAULT_KEYMAP).items()}
        self.repeat_delay_s = float(repeat_delay_s)
        self.repeat_interval_s = float(repeat_interval_s)
        self._repeat = None        # (note, key_name, next_fire_time)

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
            self._repeat = None                      # any new key cancels a repeat
            if not (self.bases[0] <= note <= self.bases[-1] + self._span):
                return "swallow"
            off = (note - self.lo) % self._span
            if off == 0:                             # a C: page step, relative to entry C
                return "prev" if note <= self.base else "next"
            name = self.keymap.get(off)
            if name is None:
                return "swallow"
            if name in REPEATING:
                self._repeat = (note, name, now + self.repeat_delay_s)
            return "key:" + name
        return self._advance_handshake(note, now)

    def note_off(self, note, now):
        self.touch(now)
        self._held.discard(note)
        self._update_chord(now)
        if self._repeat is not None and self._repeat[0] == note:
            self._repeat = None
        return "swallow" if self.in_control else "forward"

    def poll_repeat(self, now):
        """Key names due from a held arrow (at most one per poll)."""
        if not self.in_control or self._repeat is None:
            return []
        note, name, due = self._repeat
        if now < due:
            return []
        self._repeat = (note, name, now + self.repeat_interval_s)
        return [name]

    def tick(self, now):
        """Poll from the worker loop. Returns True when the chord-hold just
        ended control mode."""
        if self.in_control and self._chord_since is not None \
                and now - self._chord_since >= self.hold_s:
            self.in_control = False
            self._chord_since = None
            self._repeat = None
            self._reset_seq()
            return True
        return False

    # ----- indicator ----------------------------------------------------
    def indicator(self, now, source, connected=True):
        """(text, reverse). source: 'BT' | 'USB' | None (no input port at all).
        connected=False means the keyboard's actual link is down: the cell
        becomes reverse-video " BT OFF ", blinking at blink_s unless
        offline_blink is off (then it stays solid reverse)."""
        if source is None:
            return "BT off", False
        if not connected:
            on = True if not self.offline_blink else (int(now / self.blink_s) % 2 == 0)
            return f" {source} OFF ", on
        if self.in_control:
            return f" {source} CTRL ", True
        if self._knob_warn_t is not None and now - self._knob_warn_t < self.warn_s:
            if self._knob_value is None:      # not seen since startup: position unknown
                return " TURN KNOB UP ", True
            return f" KNOB UNDER {self.prefix_min} ", True
        flashing = self._last_msg_t is not None and now - self._last_msg_t < self.flash_s
        dot = "*" if flashing else " "
        return f"{source} ch{self.target_channel()} {dot}", False

    # ----- on-screen help ------------------------------------------------
    def help_lines(self):
        """Short how-to for the Help page and the Esc-menu overlay, generated
        from the LIVE bindings so the text can never drift from the behaviour.
        C = the low C, C' = the C an octave above it."""
        names = "C C# D D# E F F# G G# A A# B".split()
        label = {"KEY_UP": "up", "KEY_DOWN": "down", "KEY_LEFT": "left",
                 "KEY_RIGHT": "right", "KEY_ENTER": "enter", "KEY_ESCAPE": "esc/menu"}
        keys = [(names[(self.lo + off) % 12], label.get(k, k.replace("KEY_", "").lower()), k)
                for off, k in sorted(self.keymap.items())]
        arrows = "  ".join(f"{n} {l}" for n, l, k in keys if k in REPEATING)
        others = "  ".join(f"{n} {l}" for n, l, k in keys if k not in REPEATING)
        octs = sorted(b // 12 - 1 for b in self.bases)
        span = f"octave {octs[0]}" if len(octs) == 1 else f"octaves {octs[0]}-{octs[-1]}"
        lines = ["BT KEYBOARD CONTROL",
                 "enter: knob up, then C C C  C' C' C'  C"]
        if arrows:
            lines.append(arrows)
        if others:
            lines.append(others)
        lines.append("C prev page  C' next page")
        lines.append(f"exit: hold C + C' {self.hold_s:g}s  ({span})")
        return lines

    # ----- internals ----------------------------------------------------
    def _update_chord(self, now):
        if any(self._held == {b, b + self._span} for b in self.bases):
            if self._chord_since is None:
                self._chord_since = now
        else:
            self._chord_since = None

    def _advance_handshake(self, note, now):
        """Sliding match: the LAST len(HANDSHAKE) taps must be the pattern,
        inside window_s, with the knob up when the final tap lands. A fumbled
        start (one tap too many) therefore still enters; any stray note sits
        in the ring and breaks the match until it scrolls out."""
        self._taps.append((note, now))
        self._taps = self._taps[-len(HANDSHAKE):]
        if len(self._taps) < len(HANDSHAKE):
            return "forward"
        base = None
        for b in self.bases:                         # any octave's C pair will do
            kinds = tuple("lo" if n == b else "hi" if n == b + self._span else "x"
                          for n, _ in self._taps)
            if kinds == HANDSHAKE:
                base = b
                break
        if base is None:
            return "forward"
        if now - self._taps[0][1] > self.window_s:
            return "forward"
        if self._knob_value is None or self._knob_value < self.prefix_min:
            # right combo, knob not up: remind on the footer for warn_s
            self._knob_warn_t = now
            return "forward"
        self.base = base
        self._reset_seq()
        self.in_control = True
        self._chord_since = None
        return "enter"

    def _reset_seq(self):
        self._taps = []
