from __future__ import annotations

import time
from heapq import nsmallest
from collections import deque
from typing import Any
import os


TRACE_LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "log.txt")
MAX_STEPS_PER_FRAME = 8


class PianoRollState:
    def __init__(self, ticks_per_col: int, idle_scroll_bpm: float, out_range_hold: float) -> None:
        self.ticks_per_col = max(1, int(ticks_per_col))
        self.idle_scroll_bpm = float(idle_scroll_bpm)
        self.out_range_hold = float(out_range_hold)

        # active[(ch, pitch)] = (velocity, start_tick)
        self.active: dict[tuple[int, int], tuple[int, int]] = {}
        self.cols_buf: deque[list[tuple[int, int, int]]] = deque()
        self.time_cols = 0
        # completed spans: (start_tick, end_tick, pitch, ch, vel)
        self.spans: deque[tuple[int, int, int, int, int]] = deque()

        self.last_tick = 0
        self.last_raw_tick = 0.0
        self.last_time: float | None = None
        self.last_raw_time: float | None = None
        self.last_run_bpm = float(idle_scroll_bpm)

        self.recent_hits: deque[tuple[int, int, int, float]] = deque(maxlen=256)
        self.last_above: tuple[int, int, float] | None = None
        self.last_below: tuple[int, int, float] | None = None

    def _append_trace(self, now: float, *, steps: int, loop_ms: float) -> None:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        line = (
            f"[{timestamp}] [pianoroll] steps={int(steps)} active={len(self.active)} "
            f"recent_hits={len(self.recent_hits)} loop_ms={loop_ms:.3f}\n"
        )
        try:
            with open(TRACE_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def _sample_offsets(self, total_steps: int, sample_count: int) -> list[int]:
        if sample_count <= 0:
            return []
        if sample_count >= total_steps:
            return list(range(total_steps))
        # Evenly distribute samples; always include the first and last logical column.
        max_step_index = total_steps - 1
        offsets = set()
        for i in range(sample_count):
            offsets.add((i * max_step_index) // (sample_count - 1))
        return sorted(offsets)

    def _append_columns(self, *, steps: int, now: float, overlay_window: float) -> float:
        if steps <= 0:
            return 0.0

        loop_start = time.perf_counter()
        if steps <= MAX_STEPS_PER_FRAME:
            offsets = range(steps)
        else:
            offsets = self._sample_offsets(steps, MAX_STEPS_PER_FRAME)

        for offset in offsets:
            col_now = now
            if steps > 1:
                col_now = now - (((steps - 1 - int(offset)) / float(steps)) * overlay_window)

            now_col = [(pitch, ch, vel) for (ch, pitch), (vel, _start) in self.active.items()]
            cutoff = col_now - overlay_window
            recent = [(p, ch, v) for (p, ch, v, ts) in list(self.recent_hits) if ts >= cutoff]
            if recent:
                now_col.extend(recent)
            self.cols_buf.append(now_col)

        return (time.perf_counter() - loop_start) * 1000.0

    def _reset_transport_history(self) -> None:
        """Clear non-memory visual history when a new transport run starts."""
        self.active.clear()
        self.spans.clear()
        self.recent_hits.clear()
        self.last_above = None
        self.last_below = None
        self.last_tick = 0
        self.last_raw_tick = 0.0
        self.last_time = None
        self.last_raw_time = None
        if self.time_cols > 0:
            self.cols_buf = deque([[] for _ in range(self.time_cols)], maxlen=self.time_cols)
        else:
            self.cols_buf = deque()

    def _close_active(self, ch: int, note: int, end_tick: int) -> None:
        active = self.active.pop((ch, note), None)
        if not active:
            return
        vel, start_tick = active
        if end_tick < start_tick:
            end_tick = start_tick
        self.spans.append((start_tick, end_tick, note, ch, vel))

    def _tick_now(self) -> int:
        return int(round(self.last_raw_tick))

    def on_midi_event(self, msg: Any, pitch_low: int, pitch_high: int, now: float | None = None) -> None:
        now = time.time() if now is None else float(now)
        kind = getattr(msg, "type", "")

        if kind == "start":
            self._reset_transport_history()
            return

        if kind == "note_on":
            ch = int(getattr(msg, "channel", 0)) + 1
            note = int(getattr(msg, "note", -1))
            vel = int(getattr(msg, "velocity", 0))
            if vel > 0:
                # retrigger closes previous span first
                now_tick = self._tick_now()
                self._close_active(ch, note, end_tick=now_tick)
                self.active[(ch, note)] = (vel, now_tick)
                self.recent_hits.append((note, ch, vel, now))
                if note > pitch_high:
                    self.last_above = (note, ch, now)
                elif note < pitch_low:
                    self.last_below = (note, ch, now)
            else:
                self._close_active(ch, note, end_tick=self._tick_now())
            return

        if kind == "note_off":
            ch = int(getattr(msg, "channel", 0)) + 1
            note = int(getattr(msg, "note", -1))
            self._close_active(ch, note, end_tick=self._tick_now())
            return

        if kind == "control_change" and int(getattr(msg, "control", -1)) == 123:
            ch = int(getattr(msg, "channel", 0)) + 1
            keys = [k for k in self.active.keys() if k[0] == ch]
            for key in keys:
                self._close_active(key[0], key[1], end_tick=self._tick_now())
            return

        if kind == "stop":
            keys = list(self.active.keys())
            for key in keys:
                self._close_active(key[0], key[1], end_tick=self._tick_now())
            self.recent_hits.clear()

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
            self.last_raw_tick = float(tick)
            self.last_raw_time = now
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
            if self.last_raw_time is None:
                self.last_raw_time = now
            raw_elapsed = now - self.last_raw_time
            if raw_elapsed > 0:
                self.last_raw_tick += raw_elapsed * ticks_per_sec
                self.last_raw_time = now
            elapsed = now - float(self.last_time)
            virtual_ticks = elapsed * ticks_per_sec
            if virtual_ticks >= self.ticks_per_col:
                steps = max(1, int(virtual_ticks // self.ticks_per_col))
                consumed = steps * self.ticks_per_col / ticks_per_sec
                self.last_time = float(self.last_time) + consumed

        col_secs = self._column_seconds(running=running, bpm=bpm)
        overlay_window = max(0.05, min(0.25, col_secs))

        loop_ms = self._append_columns(steps=steps, now=now, overlay_window=overlay_window)
        self._append_trace(now, steps=steps, loop_ms=loop_ms)

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

        tick_now = self._tick_now()
        active_items = self.active.items()
        recent_hits_list = list(self.recent_hits)

        visible_cols = self._visible_cols(roll_cols)
        overlay = [(p, ch, v) for (p, ch, v, ts) in recent_hits_list if (now - ts) <= 0.25]
        if overlay and visible_cols:
            visible_cols[-1] = list(visible_cols[-1]) + overlay

        sort_key = lambda item: (item[0][1], item[0][0])
        if max_active_notes <= 0:
            selected_active: list[tuple[tuple[int, int], tuple[int, int]]] = []
        elif len(self.active) > max_active_notes:
            selected_active = nsmallest(max_active_notes, active_items, key=sort_key)
        else:
            selected_active = sorted(active_items, key=sort_key)
        active_notes_payload = [[ch, pitch, vel] for (ch, pitch), (vel, _start) in selected_active]

        recent_hits_payload = []
        for pitch, ch, vel, ts in recent_hits_list[-max_recent_hits:]:
            recent_hits_payload.append([pitch, ch, vel, int(max(0.0, now - ts) * 1000.0)])

        above_pitches = {
            note
            for (note, _ch, _vel, ts) in recent_hits_list
            if (now - ts) <= self.out_range_hold and note > pitch_high
        }
        below_pitches = {
            note
            for (note, _ch, _vel, ts) in recent_hits_list
            if (now - ts) <= self.out_range_hold and note < pitch_low
        }
        for (_ch, note), (_vel, _start) in active_items:
            if note > pitch_high:
                above_pitches.add(note)
            elif note < pitch_low:
                below_pitches.add(note)

        tick_right = int(self.last_tick)
        tick_left = tick_now - max(1, int(roll_cols) - 1) * self.ticks_per_col
        tick_right_edge = tick_now + self.ticks_per_col
        prune_before = tick_left - self.ticks_per_col
        while self.spans and self.spans[0][1] < prune_before:
            self.spans.popleft()

        spans: list[list[int]] = []
        for start, end, pitch, ch, vel in self.spans:
            if pitch < pitch_low or pitch > pitch_high:
                continue
            if end < tick_left or start > tick_right_edge:
                continue
            spans.append([int(start), int(end), int(pitch), int(ch), int(vel)])
        for (ch, pitch), (vel, start) in active_items:
            if pitch < pitch_low or pitch > pitch_high:
                continue
            end = tick_now
            if end < tick_left or start > tick_right_edge:
                continue
            spans.append([int(start), int(end), int(pitch), int(ch), int(vel)])

        return {
            "time_cols": int(self.time_cols),
            "tick_right": tick_right,
            "tick_now": tick_now,
            "active_count": int(len(self.active)),
            "active_notes": active_notes_payload,
            "recent_hits": recent_hits_payload,
            "spans": spans,
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
