# pages/ports.py — show current ALSA MIDI ports
PAGE_ID = 2
PAGE_NAME = "Ports"

import subprocess
from midicrt import draw_line, SCREEN_ROWS

def draw(state):
    draw_line(0, f"[{PAGE_ID}] {PAGE_NAME} — aconnect -l")
    try:
        result = subprocess.run(["aconnect", "-l"], capture_output=True, text=True)
        lines = result.stdout.strip().splitlines()
        for i, l in enumerate(lines[:SCREEN_ROWS - 2]):
            draw_line(2 + i, l)
    except Exception as e:
        draw_line(2, f"Error: {e}")
