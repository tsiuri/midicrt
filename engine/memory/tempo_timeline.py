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
