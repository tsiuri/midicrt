# pages/matrix1000_presets.py — Matrix-1000 factory preset browser.
#
# A scrollable grid of all factory patch names (one bank of 100 at a time,
# 4 columns x 25). The cursor cell is backlit (reverse text). Enter loads the
# selected preset into the unit (bank select + program change on the Matrix
# channel). [ / ] page through the 10 banks.
#
# Reachable from the Esc menu or +/- page cycling. Shares the Matrix channel
# from settings "matrix1000".

BACKGROUND = False
PAGE_ID = 22
PAGE_NAME = "M1K Presets"
DEVICE_ID = "matrix1000-presets"

import time

import mido

from midicrt import draw_line
from configutil import load_section, save_section
from ui.model import CanvasWidget
from devices import matrix1000 as DEV
from devices.matrix1000_names import FACTORY_PATCH_NAMES

_cfg = {}
try:
    _cfg = load_section("matrix1000") or {}
except Exception:
    _cfg = {}

channel = int(_cfg.get("channel", DEV.DEFAULT_CHANNEL))
note_target_channel = channel   # knobctl plays the SMK-25 to this channel
bank = int(_cfg.get("bank", 0))            # 0-9, shared with the editor page
cursor = int(_cfg.get("program", 0))       # 0-99 within the bank
output_hints = _cfg.get("output_hints", ["UX16", "USB MIDI", "MIDI 1"])

COLS, ROWS = 4, 25   # 100 presets per bank
out_port = None
out_err = ""
status_msg = ""
status_time = 0.0
_save_pending = 0.0


def _mark_save():
    global _save_pending
    _save_pending = time.time() + 2.0


def _flush_save():
    global _save_pending
    if _save_pending and time.time() >= _save_pending:
        _save_pending = 0.0
        try:
            cur = load_section("matrix1000") or {}
            cur["bank"] = bank
            cur["program"] = cursor
            cur["channel"] = channel
            save_section("matrix1000", cur)
        except Exception:
            pass


def _status(t):
    global status_msg, status_time
    status_msg = t
    status_time = time.time()


def _ensure_out():
    global out_port, out_err
    if out_port is not None:
        return True
    try:
        names = list(mido.get_output_names())
    except Exception as exc:
        out_err = f"midi backend: {exc}"
        return False
    for hint in output_hints:
        hl = str(hint).lower()
        for name in names:
            if hl in name.lower():
                try:
                    out_port = mido.open_output(name)
                    out_err = ""
                    return True
                except Exception as exc:
                    out_err = f"open {name}: {exc}"
    if not out_err:
        out_err = f"no output matching {output_hints}"
    return False


def _load_selected():
    if not _ensure_out():
        _status(f"TX FAILED: {out_err}")
        return
    try:
        out_port.send(mido.Message("sysex", data=list(DEV.bank_select_sysex(bank))))
        out_port.send(mido.Message("sysex", data=list(DEV.bank_select_sysex(bank))))
        out_port.send(mido.Message("program_change", program=cursor & 0x7F,
                                   channel=(channel - 1) & 0x0F))
        idx = bank * 100 + cursor
        nm = FACTORY_PATCH_NAMES[idx] if 0 <= idx < len(FACTORY_PATCH_NAMES) else ""
        _status(f"loaded {bank}{cursor:02d}  {nm}")
    except Exception as exc:
        _status(f"TX FAILED: {exc}")


def keypress(key):
    global cursor, bank, channel, note_target_channel
    kname = key.name if getattr(key, "is_sequence", False) else ""
    s = "" if kname else str(key)
    row, col = cursor % ROWS, cursor // ROWS
    if kname == "KEY_UP":
        row = (row - 1) % ROWS
        cursor = col * ROWS + row
        _mark_save(); return True
    if kname == "KEY_DOWN":
        row = (row + 1) % ROWS
        cursor = col * ROWS + row
        _mark_save(); return True
    if kname == "KEY_LEFT":
        col = (col - 1) % COLS
        cursor = col * ROWS + row
        _mark_save(); return True
    if kname == "KEY_RIGHT":
        col = (col + 1) % COLS
        cursor = col * ROWS + row
        _mark_save(); return True
    if kname == "KEY_ENTER" or s in ("\r", "\n"):
        _load_selected(); return True
    if s == "]":
        bank = (bank + 1) % 10
        _mark_save(); return True
    if s == "[":
        bank = (bank - 1) % 10
        _mark_save(); return True
    if s == ",":
        channel = max(1, channel - 1); note_target_channel = channel; _mark_save(); return True
    if s == ".":
        channel = min(16, channel + 1); note_target_channel = channel; _mark_save(); return True
    return False


def _paint(canvas):
    from fb.canvas import GREEN_BRIGHT, GREEN_DIM, GREEN_MID, BLACK
    cw, ch = canvas.cw, canvas.ch
    # header
    idx = bank * 100 + cursor
    nm = FACTORY_PATCH_NAMES[idx] if 0 <= idx < len(FACTORY_PATCH_NAMES) else ""
    canvas.text(canvas.col_px(1), canvas.row_px(0),
                f"Matrix-1000 PRESETS   bank {bank}   ch{channel:02d}   "
                f"selected {bank}{cursor:02d}: {nm}", fg=GREEN_BRIGHT)
    canvas.text(canvas.col_px(1), canvas.row_px(1),
                "arrows:move  Enter:LOAD to unit  [/]:bank  ,/.:ch", fg=GREEN_DIM)
    if status_msg and time.time() - status_time < 6.0:
        canvas.text(canvas.col_px(1), canvas.row_px(2), status_msg, fg=GREEN_MID)

    top = 3            # grid starts at content row 3
    col_w = 24         # chars per column
    for c in range(COLS):
        for r in range(ROWS):
            p = c * ROWS + r
            num = bank * 100 + p
            name = FACTORY_PATCH_NAMES[num] if 0 <= num < len(FACTORY_PATCH_NAMES) else ""
            cell = f"{bank}{p:02d} {name:<8.8s}"
            x = canvas.col_px(1 + c * col_w)
            y = canvas.row_px(top + r)
            if p == cursor:
                # backlit reverse-text selection
                canvas.rect(x - 2, y - 1, (len(cell) + 1) * cw, ch + 1, GREEN_BRIGHT)
                canvas.text(x, y, cell, fg=BLACK, bg=GREEN_BRIGHT)
            else:
                canvas.text(x, y, cell, fg=GREEN_MID)


def _lines():
    idx = bank * 100 + cursor
    nm = FACTORY_PATCH_NAMES[idx] if 0 <= idx < len(FACTORY_PATCH_NAMES) else ""
    # minimal tty fallback (the CRT uses the painter)
    out = [f"Matrix-1000 presets  bank {bank}  ch{channel:02d}  "
           f"selected {bank}{cursor:02d}: {nm}",
           "arrows:move  Enter:load  [/]:bank"]
    for r in range(ROWS):
        cells = []
        for c in range(COLS):
            p = c * ROWS + r
            num = bank * 100 + p
            name = FACTORY_PATCH_NAMES[num] if 0 <= num < len(FACTORY_PATCH_NAMES) else ""
            mark = ">" if p == cursor else " "
            cells.append(f"{mark}{bank}{p:02d} {name:<8.8s}")
        out.append("  ".join(cells))
    return out


def draw(state):
    _flush_save()
    cols = state["cols"]
    y0 = state.get("y_offset", 3)
    for idx, line in enumerate(_lines()):
        draw_line(y0 + idx, line[:cols])


def build_widget(state):
    _flush_save()
    return CanvasWidget(page_id=PAGE_ID, page_name=PAGE_NAME,
                        lines=[""] * 28, painters=(_paint,))
