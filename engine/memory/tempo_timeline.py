from __future__ import annotations

from bisect import bisect_right

from engine.memory.session_model import SessionModel, TempoSegment


class TempoTimeline:
    """Piecewise tempo conversion helper for session tick/seconds projection."""

    def __init__(self, *, anchor_tick: int, ppqn: int, segments: list[tuple[int, float]]):
        self.anchor_tick = int(anchor_tick)
        self.ppqn = max(1, int(ppqn))

        normalized: list[tuple[int, float]] = []
        for tick, bpm in segments:
            t = int(tick)
            b = float(bpm)
            if b <= 0:
                continue
            if normalized and normalized[-1][0] == t:
                normalized[-1] = (t, b)
            else:
                normalized.append((t, b))
        if not normalized:
            normalized = [(self.anchor_tick, 120.0)]
        normalized.sort(key=lambda x: x[0])

        if normalized[0][0] > self.anchor_tick:
            normalized.insert(0, (self.anchor_tick, normalized[0][1]))
        elif normalized[0][0] < self.anchor_tick:
            bpm0 = normalized[0][1]
            for t, b in normalized:
                if t <= self.anchor_tick:
                    bpm0 = b
                else:
                    break
            normalized.insert(0, (self.anchor_tick, bpm0))

        dedup: list[tuple[int, float]] = []
        for t, b in normalized:
            if dedup and dedup[-1][0] == t:
                dedup[-1] = (t, b)
            else:
                dedup.append((t, b))
        self.segments = dedup
        self._starts = [t for t, _ in self.segments]

        self._prefix_seconds = [0.0]
        for i, (start, bpm) in enumerate(self.segments):
            nxt = self.segments[i + 1][0] if i + 1 < len(self.segments) else None
            if nxt is None:
                self._prefix_seconds.append(self._prefix_seconds[-1])
                continue
            dtick = max(0, int(nxt) - int(start))
            sec_per_tick = 60.0 / (float(bpm) * float(self.ppqn))
            self._prefix_seconds.append(self._prefix_seconds[-1] + dtick * sec_per_tick)

    @classmethod
    def from_session(cls, session: SessionModel) -> "TempoTimeline":
        header = session.header
        anchor = int(header.start_tick)
        ppqn = max(1, int(header.ppqn or 24))
        segments = [(int(s.start_tick), float(s.bpm)) for s in list(header.tempo_segments or []) if float(getattr(s, "bpm", 0.0)) > 0.0]
        if not segments:
            bpm = float(header.bpm or 120.0)
            segments = [(anchor, bpm if bpm > 0 else 120.0)]
        return cls(anchor_tick=anchor, ppqn=ppqn, segments=segments)

    def _segment_index_for_tick(self, tick: int) -> int:
        idx = bisect_right(self._starts, int(tick)) - 1
        if idx < 0:
            return 0
        return min(idx, len(self.segments) - 1)

    def tick_to_seconds(self, tick: int | float) -> float:
        t = float(tick)
        if t <= float(self.anchor_tick):
            idx0 = 0
            start0, bpm0 = self.segments[idx0]
            return (t - float(self.anchor_tick)) * (60.0 / (float(bpm0) * float(self.ppqn)))

        ti = int(t)
        idx = self._segment_index_for_tick(ti)
        seg_start, bpm = self.segments[idx]
        base = self._prefix_seconds[idx]
        sec_per_tick = 60.0 / (float(bpm) * float(self.ppqn))
        return base + (t - float(seg_start)) * sec_per_tick

    def project_tick(self, tick: int | float, *, current_bpm: float, anchor_tick: int) -> float:
        bpm = float(current_bpm) if float(current_bpm) > 0 else 120.0
        elapsed_seconds = self.tick_to_seconds(tick)
        ticks_per_second = bpm * float(self.ppqn) / 60.0
        return float(anchor_tick) + elapsed_seconds * ticks_per_second


def project_tick_with_session_tempo(session: SessionModel, tick: int | float, *, current_bpm: float, anchor_tick: int) -> float:
    timeline = TempoTimeline.from_session(session)
    return timeline.project_tick(tick, current_bpm=current_bpm, anchor_tick=anchor_tick)
from typing import Any


def _safe_float(value: Any, default: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    return out


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def normalize_tempo_segments(segments: Any, *, fallback_bpm: float = 120.0) -> list[tuple[int, float]]:
    """Normalize raw tempo segments into sorted `(start_tick, bpm)` pairs.

    Invalid entries are ignored. Duplicate `start_tick` values are deduplicated,
    keeping the last valid bpm observed for that tick.
    """

    fallback = max(1e-6, _safe_float(fallback_bpm, 120.0))
    if not isinstance(segments, (list, tuple)):
        return []

    by_tick: dict[int, float] = {}
    for seg in segments:
        if isinstance(seg, dict):
            tick_raw = seg.get("start_tick")
            bpm_raw = seg.get("bpm")
        else:
            tick_raw = getattr(seg, "start_tick", None)
            bpm_raw = getattr(seg, "bpm", None)

        tick = _safe_int(tick_raw, 0)
        bpm = _safe_float(bpm_raw, fallback)
        if bpm <= 0:
            continue
        by_tick[tick] = bpm

    return sorted(by_tick.items(), key=lambda item: item[0])


def _active_bpm_at(tick: int, segments: list[tuple[int, float]], fallback_bpm: float) -> float:
    bpm = max(1e-6, float(fallback_bpm))
    for seg_tick, seg_bpm in segments:
        if seg_tick > tick:
            break
        bpm = seg_bpm
    return bpm


def tick_to_seconds(
    tick: int,
    *,
    start_tick: int,
    ppqn: int,
    segments: Any,
    fallback_bpm: float,
) -> float:
    """Convert an absolute tick into seconds relative to `start_tick`."""

    start = int(start_tick)
    target = max(int(tick), start)
    ticks_left = target - start
    if ticks_left <= 0:
        return 0.0

    ppqn_safe = max(1, int(ppqn))
    norm = normalize_tempo_segments(segments, fallback_bpm=fallback_bpm)
    if not norm:
        bpm = max(1e-6, float(fallback_bpm or 120.0))
        return (ticks_left / ppqn_safe) * (60.0 / bpm)

    seconds = 0.0
    cursor = start
    active_bpm = _active_bpm_at(start, norm, max(1e-6, float(fallback_bpm or 120.0)))

    for seg_tick, seg_bpm in norm:
        if seg_tick <= cursor:
            active_bpm = seg_bpm
            continue
        if seg_tick >= target:
            break
        delta = seg_tick - cursor
        seconds += (delta / ppqn_safe) * (60.0 / max(1e-6, active_bpm))
        cursor = seg_tick
        active_bpm = seg_bpm

    if cursor < target:
        delta = target - cursor
        seconds += (delta / ppqn_safe) * (60.0 / max(1e-6, active_bpm))

    return float(seconds)


def seconds_to_tick(
    seconds: float,
    *,
    start_tick: int,
    ppqn: int,
    segments: Any,
    fallback_bpm: float,
) -> int:
    """Convert seconds since `start_tick` into an absolute tick."""

    remain = max(0.0, float(seconds or 0.0))
    start = int(start_tick)
    ppqn_safe = max(1, int(ppqn))
    norm = normalize_tempo_segments(segments, fallback_bpm=fallback_bpm)

    if not norm:
        bpm = max(1e-6, float(fallback_bpm or 120.0))
        delta_ticks = int(round(remain * ppqn_safe * (bpm / 60.0)))
        return start + max(0, delta_ticks)

    cursor = start
    active_bpm = _active_bpm_at(start, norm, max(1e-6, float(fallback_bpm or 120.0)))

    future = [(tick, bpm) for tick, bpm in norm if tick > start]
    for seg_tick, seg_bpm in future:
        span_ticks = seg_tick - cursor
        if span_ticks > 0:
            span_seconds = (span_ticks / ppqn_safe) * (60.0 / max(1e-6, active_bpm))
            if remain <= span_seconds:
                delta = int(round(remain * ppqn_safe * (active_bpm / 60.0)))
                return cursor + max(0, delta)
            remain -= span_seconds
            cursor = seg_tick
        active_bpm = seg_bpm

    delta = int(round(remain * ppqn_safe * (active_bpm / 60.0)))
    return cursor + max(0, delta)


def duration_seconds(
    start_tick: int,
    stop_tick: int,
    *,
    ppqn: int,
    segments: Any,
    fallback_bpm: float,
) -> float:
    """Compute segment-aware duration in seconds between two ticks."""

    start = int(start_tick)
    stop = max(int(stop_tick), start)
    if stop == start:
        return 0.0
    return tick_to_seconds(
        stop,
        start_tick=start,
        ppqn=ppqn,
        segments=segments,
        fallback_bpm=fallback_bpm,
    )
