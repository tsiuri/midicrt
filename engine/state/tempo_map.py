from __future__ import annotations

import math
from collections import Counter, deque
from dataclasses import dataclass
from typing import Any

PPQN = 24
DEFAULT_METER = "4/4"


@dataclass
class TempoSnapshot:
    running: bool
    tick_counter: int
    bar_counter: int
    beat_in_bar: int
    tick_in_beat: int
    bpm: float
    clock_interval_ms: float
    jitter_rms: float
    meter_estimate: str
    confidence: float


class TempoMap:
    """Realtime transport map derived from MIDI realtime clock + advisory meter hints."""

    def __init__(self, *, interval_window: int = 24) -> None:
        self._intervals = deque(maxlen=max(2, int(interval_window)))
        self.reset()

    def reset(self) -> None:
        self.running = False
        self.tick_counter = 0
        self.bar_counter = 0
        self._last_clock_ts: float | None = None
        self._meter = DEFAULT_METER
        self._confidence = 0.0
        self._bpm = 0.0
        self._clock_interval_ms = 0.0
        self._jitter_rms = 0.0

    def handle(self, kind: str, timestamp: float, meter_candidates: list[dict[str, Any]] | None = None) -> None:
        if kind == "start":
            self.tick_counter = 0
            self.bar_counter = 0
            self.running = True
            self._last_clock_ts = None
            self._intervals.clear()
            self._bpm = 0.0
            self._clock_interval_ms = 0.0
            self._jitter_rms = 0.0
            self._apply_meter_candidates(meter_candidates)
            return

        if kind == "continue":
            self.running = True
            self._last_clock_ts = None
            self._apply_meter_candidates(meter_candidates)
            return

        if kind == "stop":
            self.running = False
            self._last_clock_ts = None
            return

        if kind != "clock":
            self._apply_meter_candidates(meter_candidates)
            return

        if not self.running:
            return

        self._apply_meter_candidates(meter_candidates)
        self.tick_counter += 1

        meter_beats, meter_denom = _parse_meter(self._meter)
        ticks_per_beat = max(1, int(PPQN * (4.0 / meter_denom)))
        ticks_per_bar = max(1, meter_beats * ticks_per_beat)
        self.bar_counter = self.tick_counter // ticks_per_bar

        if self._last_clock_ts is not None:
            interval = max(0.0, timestamp - self._last_clock_ts)
            self._intervals.append(interval)
            if self._intervals:
                avg = sum(self._intervals) / len(self._intervals)
                if avg > 0:
                    self._bpm = 60.0 / (PPQN * avg)
                    self._clock_interval_ms = avg * 1000.0
                    variance = sum((v - avg) ** 2 for v in self._intervals) / len(self._intervals)
                    self._jitter_rms = math.sqrt(max(0.0, variance)) * 1000.0
        self._last_clock_ts = timestamp

    def snapshot(self) -> TempoSnapshot:
        meter_beats, meter_denom = _parse_meter(self._meter)
        ticks_per_beat = max(1, int(PPQN * (4.0 / meter_denom)))
        ticks_per_bar = max(1, meter_beats * ticks_per_beat)
        tick_in_bar = self.tick_counter % ticks_per_bar
        beat_in_bar = tick_in_bar // ticks_per_beat
        tick_in_beat = tick_in_bar % ticks_per_beat
        return TempoSnapshot(
            running=self.running,
            tick_counter=self.tick_counter,
            bar_counter=self.bar_counter,
            beat_in_bar=beat_in_bar,
            tick_in_beat=tick_in_beat,
            bpm=self._bpm,
            clock_interval_ms=self._clock_interval_ms,
            jitter_rms=self._jitter_rms,
            meter_estimate=self._meter,
            confidence=self._confidence,
        )

    def _apply_meter_candidates(self, meter_candidates: list[dict[str, Any]] | None) -> None:
        if not meter_candidates:
            return
        weighted = Counter()
        best_conf = 0.0
        for candidate in meter_candidates:
            if not isinstance(candidate, dict):
                continue
            labels = candidate.get("labels") or []
            conf = float(candidate.get("confidence") or 0.0)
            if not labels:
                continue
            weight = conf / max(1, len(labels))
            for label in labels:
                if isinstance(label, str) and "/" in label:
                    weighted[label] += weight
            best_conf = max(best_conf, conf)
        if not weighted:
            return
        best_label, _ = weighted.most_common(1)[0]
        self._meter = best_label
        self._confidence = max(0.0, min(1.0, best_conf))


def _parse_meter(label: str) -> tuple[int, int]:
    try:
        beats_s, denom_s = str(label).split("/", 1)
        beats = max(1, int(beats_s))
        denom = max(1, int(denom_s))
        return beats, denom
    except Exception:
        return 4, 4
