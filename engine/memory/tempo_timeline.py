from __future__ import annotations

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
