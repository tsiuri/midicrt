"""fb/terminal_capture.py — Capture legacy draw() terminal output for compositor.

Pages that use the old draw() path write ANSI escape sequences to
sys.stdout.  This module provides a file-like buffer that intercepts
those writes, parses cursor-position and erase sequences, and builds a
plain character grid that the compositor can render.

Only the sequences actually emitted by blessed + midicrt pages are
handled; the rest are silently discarded.
"""

from __future__ import annotations
import io
import re


# Matches a complete CSI (Control Sequence Introducer) sequence.
# Captures parameter string and the final byte (command letter).
_CSI_RE = re.compile(r"\x1b\[([0-9;]*)([A-Za-z])")

# Matches the OSC / other non-CSI escape sequences we want to skip.
_ESC_OTHER_RE = re.compile(r"\x1b[^[]")


class TerminalCapture(io.RawIOBase):
    """Stdout replacement that captures ANSI terminal output into a char grid.

    Usage::

        cap = TerminalCapture(cols, rows)
        sys.stdout = cap
        page.draw(state)
        sys.stdout = real_stdout
        for row_idx, row_text in cap.rows_with_content():
            compositor.draw_text_line(row_idx, row_text)
    """

    def __init__(self, cols: int, rows: int) -> None:
        self.cols = cols
        self.rows = rows
        # Character grid — list of lists for efficient mutation
        self._grid: list[list[str]] = [[" "] * cols for _ in range(rows)]
        self._row = 0
        self._col = 0
        self._buf = ""     # incomplete escape sequence accumulation

    # ------------------------------------------------------------------
    # io interface
    # ------------------------------------------------------------------

    def writable(self) -> bool:
        return True

    def write(self, s) -> int:           # type: ignore[override]
        if isinstance(s, (bytes, bytearray)):
            s = s.decode("utf-8", errors="replace")
        self._buf += s
        self._process()
        return len(s)

    def flush(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Parser
    # ------------------------------------------------------------------

    def _process(self) -> None:
        buf = self._buf
        i = 0
        while i < len(buf):
            ch = buf[i]

            if ch == "\x1b":
                # Need at least one more byte to know what kind of sequence
                if i + 1 >= len(buf):
                    break                       # wait for more data

                if buf[i + 1] == "[":
                    # CSI sequence — find terminator
                    m = _CSI_RE.match(buf, i)
                    if m is None:
                        # Incomplete — could still arrive; stop here
                        break
                    params_str = m.group(1)
                    cmd       = m.group(2)
                    self._handle_csi(params_str, cmd)
                    i = m.end()
                else:
                    # Non-CSI escape (ESC c, ESC =, etc.) — skip two bytes
                    i += 2

            elif ch == "\n":
                self._row = min(self._row + 1, self.rows - 1)
                self._col = 0
                i += 1

            elif ch == "\r":
                self._col = 0
                i += 1

            elif ch >= " " or ch == "\t":
                # Printable character (or tab — treat as single space)
                if 0 <= self._row < self.rows and 0 <= self._col < self.cols:
                    self._grid[self._row][self._col] = ch if ch != "\t" else " "
                self._col += 1
                if self._col >= self.cols:
                    self._col = 0
                    self._row = min(self._row + 1, self.rows - 1)
                i += 1

            else:
                # Other control character — skip
                i += 1

        self._buf = buf[i:]

    def _handle_csi(self, params: str, cmd: str) -> None:
        parts = params.split(";") if params else [""]

        def p(idx: int, default: int = 0) -> int:
            try:
                return int(parts[idx]) if parts[idx] else default
            except (IndexError, ValueError):
                return default

        if cmd == "H" or cmd == "f":
            # Cursor position: ESC[row;colH  (1-based)
            self._row = max(0, min(p(0, 1) - 1, self.rows - 1))
            self._col = max(0, min(p(1, 1) - 1, self.cols - 1))

        elif cmd == "A":
            # Cursor up
            self._row = max(0, self._row - max(1, p(0, 1)))

        elif cmd == "B":
            # Cursor down
            self._row = min(self.rows - 1, self._row + max(1, p(0, 1)))

        elif cmd == "C":
            # Cursor right
            self._col = min(self.cols - 1, self._col + max(1, p(0, 1)))

        elif cmd == "D":
            # Cursor left
            self._col = max(0, self._col - max(1, p(0, 1)))

        elif cmd == "K":
            # Erase in line
            mode = p(0, 0)
            if mode == 0:    # to end of line
                for c in range(self._col, self.cols):
                    self._grid[self._row][c] = " "
            elif mode == 1:  # to start of line
                for c in range(0, self._col + 1):
                    self._grid[self._row][c] = " "
            elif mode == 2:  # whole line
                self._grid[self._row] = [" "] * self.cols

        elif cmd == "J":
            # Erase in display
            mode = p(0, 0)
            if mode == 0:    # cursor to end
                for c in range(self._col, self.cols):
                    self._grid[self._row][c] = " "
                for r in range(self._row + 1, self.rows):
                    self._grid[r] = [" "] * self.cols
            elif mode == 1:  # start to cursor
                for r in range(0, self._row):
                    self._grid[r] = [" "] * self.cols
                for c in range(0, self._col + 1):
                    self._grid[self._row][c] = " "
            elif mode == 2:  # whole screen
                self._grid = [[" "] * self.cols for _ in range(self.rows)]

        # All other commands (colour, bold, etc.) are intentionally ignored.

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def rows_with_content(self, start_row: int = 0):
        """Yield (row_index, row_text) for rows that have non-blank content."""
        for r in range(start_row, self.rows):
            line = "".join(self._grid[r])
            if line.strip():
                yield r, line
