from __future__ import annotations

import time
from collections import deque
from typing import Any


class PianoRollState:
    def __init__(self, ticks_per_col: int, idle_scroll_bpm: float, out_range_hold: float) -> None:
        self.ticks_per_col = max(1, int(ticks_per_col))
        self.idle_scroll_bpm = float(idle_scroll_bpm)
        self.out_range_hold = float(out_range_hold)

        self.active: dict[tuple[int, int], int] = {}
        self.cols_buf: deque[list[tuple[int, int, int]]] = deque()
        self.time_cols = 0

        self.last_tick = 0
        self.last_time: float | None = None
        self.last_run_bpm = float(idle_scroll_bpm)

        self.recent_hits: deque[tuple[int, int, int, float]] = deque(maxlen=256)
        self.last_above: tuple[int, int, float] | None = None
        self.last_below: tuple[int, int, float] | None = None

    def on_midi_event(self, msg: Any, pitch_low: int, pitch_high: int, now: float | None = None) -> None:
        now = time.time() if now is None else float(now)
        kind = getattr(msg, "type", "")

        if kind == "note_on":
            ch = int(getattr(msg, "channel", 0)) + 1
            note = int(getattr(msg, "note", -1))
            vel = int(getattr(msg, "velocity", 0))
            if vel > 0:
                self.active[(ch, note)] = vel
                self.recent_hits.append((note, ch, vel, now))
                if note > pitch_high:
                    self.last_above = (note, ch, now)
                elif note < pitch_low:
                    self.last_below = (note, ch, now)
            else:
                self.active.pop((ch, note), None)
            return

        if kind == "note_off":
            ch = int(getattr(msg, "channel", 0)) + 1
            note = int(getattr(msg, "note", -1))
            self.active.pop((ch, note), None)
            return

        if kind == "control_change" and int(getattr(msg, "control", -1)) == 123:
            ch = int(getattr(msg, "channel", 0)) + 1
            keys = [k for k in self.active.keys() if k[0] == ch]
            for key in keys:
                self.active.pop(key, None)
            return

        if kind == "stop":
            self.active.clear()

    def on_tick(
        self,
        tick: int,
        running: bool,
        bpm: float,
        roll_cols: int,
        pitch_low: int,
        pitch_high: int,
        now: float | None = None,
    ) -> None:
        now = time.time() if now is None else float(now)
        self._ensure_cols(roll_cols)

        bpm = float(bpm or 0.0)
        if self.last_time is None:
            self.last_time = now
            if bpm > 0:
                self.last_run_bpm = bpm

        steps = 0
        if running:
            tick = int(tick)
            if tick < self.last_tick:
                self.last_tick = tick
            moved = tick - self.last_tick
            if moved >= self.ticks_per_col:
                steps = max(1, moved // self.ticks_per_col)
                self.last_tick += steps * self.ticks_per_col
            self.last_time = now
            if bpm > 0:
                self.last_run_bpm = bpm
        else:
            eff_bpm = self.last_run_bpm if self.last_run_bpm else self.idle_scroll_bpm
            ticks_per_sec = (eff_bpm * 24.0) / 60.0
            elapsed = now - float(self.last_time)
            virtual_ticks = elapsed * ticks_per_sec
            if virtual_ticks >= self.ticks_per_col:
                steps = max(1, int(virtual_ticks // self.ticks_per_col))
                consumed = steps * self.ticks_per_col / ticks_per_sec
                self.last_time = float(self.last_time) + consumed

        col_secs = self._column_seconds(running=running, bpm=bpm)
        overlay_window = max(0.05, min(0.25, col_secs))

        for _ in range(steps):
            now_col = [(pitch, ch, vel) for (ch, pitch), vel in self.active.items()]
            cutoff = now - overlay_window
            recent = [(p, ch, v) for (p, ch, v, ts) in list(self.recent_hits) if ts >= cutoff]
            if recent:
                now_col.extend(recent)
            self.cols_buf.append(now_col)

        self._refresh_out_of_range(pitch_low=pitch_low, pitch_high=pitch_high, now=now)

    def get_view_payload(
        self,
        pitch_low: int,
        pitch_high: int,
        roll_cols: int,
        now: float | None = None,
        max_active_notes: int = 64,
        max_recent_hits: int = 32,
    ) -> dict[str, Any]:
        now = time.time() if now is None else float(now)
        self._ensure_cols(roll_cols)
        self._refresh_out_of_range(pitch_low=pitch_low, pitch_high=pitch_high, now=now)

        visible_cols = self._visible_cols(roll_cols)
        overlay = [(p, ch, v) for (p, ch, v, ts) in list(self.recent_hits) if (now - ts) <= 0.25]
        if overlay and visible_cols:
            visible_cols[-1] = list(visible_cols[-1]) + overlay

        active_notes_payload = [
            [ch, pitch, vel]
            for (ch, pitch), vel in sorted(self.active.items(), key=lambda item: (item[0][1], item[0][0]))[:max_active_notes]
        ]

        recent_hits_payload = []
        for pitch, ch, vel, ts in list(self.recent_hits)[-max_recent_hits:]:
            recent_hits_payload.append([pitch, ch, vel, int(max(0.0, now - ts) * 1000.0)])

        above_pitches = {
            note
            for (note, _ch, _vel, ts) in list(self.recent_hits)
            if (now - ts) <= self.out_range_hold and note > pitch_high
        }
        below_pitches = {
            note
            for (note, _ch, _vel, ts) in list(self.recent_hits)
            if (now - ts) <= self.out_range_hold and note < pitch_low
        }
        for (_ch, note), _vel in self.active.items():
            if note > pitch_high:
                above_pitches.add(note)
            elif note < pitch_low:
                below_pitches.add(note)

        return {
            "time_cols": int(self.time_cols),
            "tick_right": int(self.last_tick),
            "active_count": int(len(self.active)),
            "active_notes": active_notes_payload,
            "recent_hits": recent_hits_payload,
            "overflow_flags": {
                "above": self.last_above is not None,
                "below": self.last_below is not None,
            },
            "overflow": {
                "above": self.last_above,
                "below": self.last_below,
                "above_count": max(0, len(above_pitches) - 1),
                "below_count": max(0, len(below_pitches) - 1),
            },
            "columns": visible_cols,
        }

    def _column_seconds(self, running: bool, bpm: float) -> float:
        eff_bpm = bpm if running and bpm > 0 else self.last_run_bpm
        if eff_bpm <= 0:
            return 0.125
        return self.ticks_per_col / (eff_bpm * 24.0 / 60.0)

    def _ensure_cols(self, roll_cols: int) -> None:
        needed = max(16, int(roll_cols))
        if self.time_cols != needed or not self.cols_buf:
            self.time_cols = needed
            self.cols_buf = deque([[] for _ in range(self.time_cols)], maxlen=self.time_cols)

    def _visible_cols(self, roll_cols: int) -> list[list[tuple[int, int, int]]]:
        if len(self.cols_buf) < roll_cols:
            return ([[] for _ in range(roll_cols - len(self.cols_buf))] + list(self.cols_buf))
        return list(self.cols_buf)[-roll_cols:]

    def _refresh_out_of_range(self, pitch_low: int, pitch_high: int, now: float) -> None:
        for (ch, pitch), _vel in self.active.items():
            if pitch > pitch_high:
                self.last_above = (pitch, ch, now)
            elif pitch < pitch_low:
                self.last_below = (pitch, ch, now)

        for (pitch, ch, _vel, ts) in list(self.recent_hits):
            if (now - ts) > self.out_range_hold:
                continue
            if pitch > pitch_high:
                self.last_above = (pitch, ch, ts)
            elif pitch < pitch_low:
                self.last_below = (pitch, ch, ts)

        if self.last_above and (now - self.last_above[2]) > self.out_range_hold:
            self.last_above = None
        if self.last_below and (now - self.last_below[2]) > self.out_range_hold:
            self.last_below = None
