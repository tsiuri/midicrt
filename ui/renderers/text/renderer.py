"""ANSI/Blessed text renderer.

TTY-only renderer: no framebuffer or X dependencies.
"""

from blessed import Terminal

from ui.model import Column, Frame, Line, PianoRollWidget, Segment, Spacer, TextBlock, Widget


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
