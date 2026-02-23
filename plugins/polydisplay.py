# plugins/polydisplay.py — polyphonic note display + unique CC tracker
import time
from midicrt import term

NOTE_NAMES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]

# per-channel state
active_notes = {ch: set() for ch in range(1, 17)}
cc_history = {ch: [] for ch in range(1, 17)}       # list of (cc_number, timestamp)
cc_last = {ch: None for ch in range(1, 17)}        # (cc, value, timestamp)

def _note_off(ch: int, note: int):
    if active_notes[ch]:
        active_notes[ch] = {(n, v) for (n, v) in active_notes[ch] if n != note}

def handle(msg):
    ch = msg.channel + 1
    now = time.time()

    if msg.type == "note_on":
        if msg.velocity == 0:
            _note_off(ch, msg.note)
        else:
            _note_off(ch, msg.note)
            active_notes[ch].add((msg.note, msg.velocity))

    elif msg.type == "note_off":
        _note_off(ch, msg.note)

    elif msg.type == "control_change":
        # record this CC
        cc_history[ch].append((msg.control, now))
        # keep only last 1s
        cc_history[ch] = [(cc, t) for (cc, t) in cc_history[ch] if now - t < 1.0]
        cc_last[ch] = (msg.control, msg.value, now)

def _fmt_note(note_num: int, vel: int) -> str:
    """Return e.g. G2(043) — note name + MIDI number"""
    name = NOTE_NAMES[note_num % 12]
    octave = (note_num // 12) - 1 + 2  # +2 octave shift
    return f"{name}{octave}({note_num:03d})"

def get_notes(ch: int) -> str:
    """Return note list + latest CC info for this channel."""
    now = time.time()

    # --- notes section ---
    notes = sorted(active_notes[ch], key=lambda n: n[0])
    note_text = " ".join([_fmt_note(n, v) for (n, v) in notes[:5]]).ljust(26)

    # --- CC section ---
    # unique CCs touched in the last second
    recent = [(cc, t) for (cc, t) in cc_history[ch] if now - t < 1.0]
    unique_ccs = {cc for (cc, _) in recent}
    cc_unique_count = len(unique_ccs)
    last = cc_last[ch]

    if last and now - last[2] < 1.0:
        cc_num, cc_val, _ = last
        txt = f"CC:{cc_num:02d}={cc_val:03d}"
        if cc_unique_count > 1:
            txt += f" [{cc_unique_count}]"
        cc_str = term.reverse(f" {txt} ") + term.normal
    else:
        cc_str = " " * 16  # constant width to avoid flicker

    return note_text + cc_str

