"""ANSI/Blessed text renderer.

TTY-only renderer: no framebuffer or X dependencies.
"""

from blessed import Terminal

from ui.model import (
    CaptureStatusWidget,
    Column,
    EventLogWidget,
    FooterStatusWidget,
    Frame,
    Line,
    MicrotimingHistogramWidget,
    ModuleHealthWidget,
    NotesWidget,
    PianoRollWidget,
    Segment,
    Spacer,
    TempoQualityWidget,
    TextBlock,
    TransportWidget,
    Widget,
)


class TextRenderer:
    def __init__(self, term: Terminal | None = None):
        self.term = term or Terminal()

    @staticmethod
    def _velocity_char(v: int) -> str:
        if v >= 100:
            return "█"
        if v >= 60:
            return "▓"
        if v > 0:
            return "░"
        return " "

    @staticmethod
    def _note_name(n: int) -> str:
        names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        n = max(0, int(n))
        return f"{names[n % 12]}{(n // 12) - 1}"

    def render(self, widget: Widget, frame: Frame) -> list[str]:
        lines = self._flatten(widget)
        out = [self._render_line(line, frame.cols) for line in lines[: frame.rows]]
        while len(out) < frame.rows:
            out.append("".ljust(frame.cols))
        return out

    def _flatten(self, widget: Widget) -> list[Line]:
        if isinstance(widget, TextBlock):
            return widget.lines
        if isinstance(widget, Spacer):
            return [Line.plain("") for _ in range(max(0, widget.rows))]
        if isinstance(widget, Column):
            out: list[Line] = []
            for child in widget.children:
                out.extend(self._flatten(child))
            return out

        if isinstance(widget, TransportWidget):
            return [
                Line.plain(f"Running: {widget.running}"),
                Line.plain(f"Bar Counter: {widget.bar}"),
                Line.plain(f"BPM: {widget.bpm:5.1f}"),
                Line.plain(f"Ticks: {widget.tick}"),
                Line.plain(widget.time_signature or "Time Signature: (no lock)"),
            ]
        if isinstance(widget, NotesWidget):
            return widget.lines
        if isinstance(widget, EventLogWidget):
            out = [Line.plain(widget.title), Line.plain(widget.filter_summary)]
            out.extend(Line.plain(e) for e in widget.entries)
            if widget.marker:
                out.append(Line.plain(widget.marker))
            return out
        if isinstance(widget, FooterStatusWidget):
            if widget.right:
                return [Line.plain(f"{widget.left} {widget.right}".strip())]
            return [Line.plain(widget.left)]

        if isinstance(widget, TempoQualityWidget):
            return [
                Line.plain("Tempo Quality"),
                Line.plain(f"BPM: {widget.bpm:6.2f}"),
                Line.plain(f"Confidence: {widget.confidence:0.2f}"),
                Line.plain(f"Stability: {widget.stability:0.2f}"),
                Line.plain(f"Lock: {widget.lock_state} {widget.meter}".rstrip()),
            ]
        if isinstance(widget, MicrotimingHistogramWidget):
            total = max(1, int(widget.total_samples or 0))
            out = [Line.plain(widget.title), Line.plain(f"Samples: {widget.total_samples}")]
            for label, count in widget.buckets:
                width = min(16, int(round((max(0, int(count)) / total) * 16)))
                bar = "█" * width + "·" * (16 - width)
                out.append(Line.plain(f"{label:>8} {bar} {count:>4}"))
            return out
        if isinstance(widget, CaptureStatusWidget):
            age = "--" if widget.last_commit_age_s is None else f"{widget.last_commit_age_s:0.1f}s"
            return [
                Line.plain("Capture"),
                Line.plain(f"Armed: {widget.armed}"),
                Line.plain(f"State: {widget.state}"),
                Line.plain(f"Target: {widget.target_path}"),
                Line.plain(f"Commit: {widget.last_commit}"),
                Line.plain(f"Commit age: {age}"),
            ]
        if isinstance(widget, ModuleHealthWidget):
            out = [Line.plain("Module Health")]
            for card in widget.cards:
                out.append(Line.plain(f"[{card.status.upper():>4}] {card.name}"))
                out.append(Line.plain(f"  lat={card.latency_ms:5.1f}ms drop={card.drop_rate:0.2%}"))
                if card.detail:
                    out.append(Line.plain(f"  {card.detail}"))
            return out
        if isinstance(widget, PianoRollWidget):
            lines: list[Line] = []
            timeline_label = f"{'Bars':>7} │"
            lines.append(Line.plain(timeline_label + widget.timeline))
            for pitch, row_cells in zip(widget.pitches, widget.cells):
                label = f"{self._note_name(pitch):>7} │"
                chars = ''.join(self._velocity_char(c.velocity) for c in row_cells)
                lines.append(Line.plain(label + chars))
            return lines
        return [Line.plain(f"[Unsupported widget: {type(widget).__name__}]")]

    def _render_line(self, line: Line, cols: int) -> str:
        text = ""
        for seg in line.segments:
            text += self._style_segment(seg)
        plain = self.term.strip_seqs(text)
        if len(plain) > cols:
            return plain[:cols]
        return text + (" " * max(0, cols - len(plain)))

    def _style_segment(self, seg: Segment) -> str:
        text = seg.text
        if seg.style.reverse:
            text = self.term.reverse(text)
        if seg.style.bold:
            text = self.term.bold(text)
        return text
