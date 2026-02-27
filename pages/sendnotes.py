# pages/sendnotes.py — simple MIDI note sender from keyboard
BACKGROUND = True
PAGE_ID = 2
PAGE_NAME = "Send Notes"

import time
import sys
from collections import deque
from blessed import Terminal
import mido
from midicrt import draw_line
from ui.model import PageLinesWidget

term = Terminal()

# State
out_port = None
out_name = "GreenCRT Sender"
channel = 1  # 1-16
octave = 4   # MIDI 60=C4 base
velocity = 96
gate_ms = 120

# active notes with expiry: deque of (when_s, note, channel)
active = deque()

# key → semitone offset mapping (QWERTY row)
KEYMAP = {
    'z': 0,  's': 1,  'x': 2,  'd': 3,  'c': 4,
    'v': 5,  'g': 6,  'b': 7,  'h': 8,  'n': 9,
    'j': 10, 'm': 11,
    ',': 12, 'l': 13, '.': 14, ';': 15, '/': 16,
}


def _ensure_out():
    global out_port
    if out_port is not None:
        return
    try:
        # Try to open an existing device with this name; otherwise create virtual
        try:
            out_port = mido.open_output(out_name)
        except (IOError, OSError):
            out_port = mido.open_output(out_name, virtual=True)
    except Exception:
        out_port = None


def _note_on(note_num, vel, ch):
    if out_port is None:
        return
    try:
        out_port.send(mido.Message('note_on', note=note_num, velocity=vel, channel=ch - 1))
    except Exception:
        pass


def _note_off(note_num, ch):
    if out_port is None:
        return
    try:
        out_port.send(mido.Message('note_off', note=note_num, velocity=0, channel=ch - 1))
    except Exception:
        pass


def _send_key(ch, base_oct, key_char):
    off = KEYMAP.get(key_char)
    if off is None:
        return False
    note_num = 12 * (base_oct + 1) + off  # C4=60 when base_oct=4 and off=0
    _note_on(note_num, velocity, ch)
    active.append((time.time() + (gate_ms / 1000.0), note_num, ch))
    return True


def keypress(chk):
    global channel, octave, velocity, gate_ms
    s = str(chk)
    if not s:
        return False

    # channel adjust
    if s == ',':
        channel = max(1, channel - 1); return True
    if s == '.':
        channel = min(16, channel + 1); return True

    # octave adjust
    if s == '[':
        octave = max(-1, octave - 1); return True
    if s == ']':
        octave = min(9, octave + 1); return True

    # velocity adjust
    if s == '-':
        velocity = max(1, velocity - 8); return True
    if s == '=':
        velocity = min(127, velocity + 8); return True

    # gate adjust
    if s.lower() == 'g':
        gate_ms = max(20, gate_ms - 20); return True
    if s.lower() == 'h':
        gate_ms = min(2000, gate_ms + 20); return True

    # send mapped note
    if s.lower() in KEYMAP:
        _ensure_out()
        return _send_key(channel, octave, s.lower())

    return False


def _expire_notes():
    now = time.time()
    while active and active[0][0] <= now:
        _, note, ch = active.popleft()
        _note_off(note, ch)


def draw(state):
    _ensure_out()
    _expire_notes()
    cols = state['cols']
    y0 = state.get('y_offset', 3)
    for idx, line in enumerate(_build_widget_lines(state)):
        draw_line(y0 + idx, line[:cols])


def _build_widget_lines(_state):
    status = (
        f"Dev: {out_name if out_port else '(not open)'}  "
        f"Ch:{channel:02d}  Oct:{octave:+d}  Vel:{velocity:03d}  Gate:{gate_ms}ms  "
        f"Active:{len(active)}"
    )
    help1 = "Keys: z s x d c v g b h n j m (, l . ; /) — white/black keys"
    help2 = "[,] ch-+/  [-]/[+] oct  [-]/[=] vel  g/h gate"
    return [f"--- {PAGE_NAME} ---", status, "", help1, help2]

def update(state):
    _expire_notes()



def build_widget(state):
    return PageLinesWidget(page_id=PAGE_ID, page_name=PAGE_NAME, lines=_build_widget_lines(state))
