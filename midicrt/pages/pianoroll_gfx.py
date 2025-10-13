# pages/pianoroll_gfx.py — hybrid graphics piano roll (terminal + pygame/fbcon)
PAGE_ID = 9
PAGE_NAME = "Piano Roll (GFX)"
background = False

import os, sys, threading, time, pygame
from blessed import Terminal
from midicrt import draw_line

term = Terminal()

# --- detect environment and set SDL for framebuffer if needed ---
if not os.environ.get("DISPLAY"):
    print("[GFX] No DISPLAY detected; using framebuffer mode.")
    os.environ["SDL_VIDEODRIVER"] = "fbcon"
    os.environ.setdefault("SDL_FBDEV", "/dev/fb0")
    os.environ.setdefault("SDL_NOMOUSE", "1")

# --- constants ---
WIN_W, WIN_H = 960, 540
GRID_COLS, GRID_ROWS = 64, 24  # columns = time, rows = pitch
NOTE_COLOR = (0, 255, 0)
BG_COLOR = (0, 0, 0)
LINE_COLOR = (0, 64, 0)
TEXT_COLOR = (0, 255, 0)

# --- state ---
_notes = {}  # (ch, note) → age
_tlast = time.time()
_gfx_ready = False
_stop = False


def handle(msg):
    """Record MIDI notes for the rolling display."""
    global _notes
    if msg.type == "note_on" and msg.velocity > 0:
        _notes[(msg.channel, msg.note)] = 0.0
    elif msg.type in ("note_off", "note_on") and (msg.channel, msg.note) in _notes:
        _notes.pop((msg.channel, msg.note), None)


def _gfx_loop():
    """Runs in a background thread; owns the pygame window."""
    global _gfx_ready, _stop

    print("[GFX] Initializing graphics window...")
    import pygame
    print("[GFX] SDL_VIDEODRIVER =", os.environ.get("SDL_VIDEODRIVER"))
    print("[GFX] SDL_FBDEV =", os.environ.get("SDL_FBDEV"))
    print("[GFX] Available drivers:", pygame.display.get_driver() if pygame.display.get_init() else "not init")

    try:
        pygame.display.init()
        driver = pygame.display.get_driver()
        print(f"[GFX] Active video driver: {driver}")
        screen = pygame.display.set_mode((WIN_W, WIN_H))
        pygame.display.set_caption("MIDI CRT — Piano Roll (GFX)")
        print("[GFX] Window opened:", screen.get_size())
    except Exception as e:
        print("[GFX] Initialization failed:", e)
        return

    clock = pygame.time.Clock()
    _gfx_ready = True

    cell_w = WIN_W // GRID_COLS
    cell_h = WIN_H // GRID_ROWS

    # Draw static grid lines
    def draw_grid():
        for y in range(GRID_ROWS):
            pygame.draw.line(
                screen, LINE_COLOR, (0, y * cell_h), (WIN_W, y * cell_h)
            )
        for x in range(GRID_COLS):
            pygame.draw.line(
                screen, LINE_COLOR, (x * cell_w, 0), (x * cell_w, WIN_H)
            )

    font = None
    try:
        font = pygame.font.Font(None, 16)
    except Exception:
        pass

    while not _stop:
        screen.fill(BG_COLOR)
        draw_grid()

        now = time.time()
        for (ch, note), age in list(_notes.items()):
            _notes[(ch, note)] = age + 0.05
            x = int((age * 6) % GRID_COLS) * cell_w
            y = GRID_ROWS - ((note % GRID_ROWS) + 1)
            rect = pygame.Rect(x, y * cell_h, cell_w, cell_h)
            pygame.draw.rect(screen, NOTE_COLOR, rect)

        # Draw footer text
        if font:
            txt = font.render(
                f"{len(_notes)} active notes   Driver: {driver}", True, TEXT_COLOR
            )
            screen.blit(txt, (8, WIN_H - 20))

        pygame.display.flip()
        clock.tick(30)

    pygame.quit()
    print("[GFX] Graphics loop stopped.")


def draw(state):
    """Invoked by the terminal UI; launches background graphics thread once."""
    global _gfx_ready, _stop
    if not _gfx_ready:
        threading.Thread(target=_gfx_loop, daemon=True).start()
        draw_line(state["y_offset"], "[GFX] Launching graphics thread...")
    else:
        draw_line(state["y_offset"], f"[GFX] Running ({len(_notes)} notes)")


def keypress(ch):
    """Handle user keys while page active."""
    global _stop
    if ch.lower() == "q":
        _stop = True
        return True
    return False
