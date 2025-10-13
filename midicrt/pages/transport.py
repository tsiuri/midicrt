# pages/transport.py — simple transport info
PAGE_ID = 3
PAGE_NAME = "Transport"

from midicrt import draw_line

def draw(state):
    draw_line(0, f"[{PAGE_ID}] {PAGE_NAME}")
    draw_line(2, f"Running: {state['running']}")
    draw_line(3, f"Bar Counter: {state['bar']}")
    draw_line(4, f"BPM: {state['bpm']:5.1f}")
    draw_line(5, f"Ticks: {state['tick']}")
