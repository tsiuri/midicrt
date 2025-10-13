# -*- coding: utf-8 -*-
# midicrt.py — CRT-style MIDI monitor / visualizer for Cirklon

import os, sys, time, glob, importlib.util, subprocess, threading
from collections import deque
from inspect import signature
from blessed import Terminal
import mido

# Explicitly import shared poly display so it's never double-loaded
import plugins.polydisplay as polydisplay

# ---------------------------------------------------------------------
# Display / timing
# ---------------------------------------------------------------------
SCREEN_COLS = 95
SCREEN_ROWS = 30
FPS = 60.0
term = Terminal()

# ---------------------------------------------------------------------
# Transport state
# ---------------------------------------------------------------------
bpm = 0.0
tick_counter = 0
bar_counter = 0
running = False
last_clock_time = None
clock_intervals = deque(maxlen=24)

# ---------------------------------------------------------------------
# Instrument names
# ---------------------------------------------------------------------
def load_instrument_names():
    path = os.path.join(os.path.dirname(__file__), "instruments.txt")
    names = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            names = [ln.strip() for ln in f if ln.strip()]
    except FileNotFoundError:
        pass
    while len(names) < 16:
        names.append(f"Channel {len(names)+1}")
    return names[:16]

INSTRUMENT_NAMES = load_instrument_names()

# ---------------------------------------------------------------------
# Plugin loader
# ---------------------------------------------------------------------
PLUGINS = []

def load_plugins():
    global PLUGINS
    PLUGINS = []
    plugindir = os.path.join(os.path.dirname(__file__), "plugins")
    for path in sorted(glob.glob(os.path.join(plugindir, "*.py"))):
        modname = os.path.splitext(os.path.basename(path))[0]
        if modname.startswith("__") or modname == "polydisplay":
            continue
        fqname = f"plugins.{modname}"
        try:
            spec = importlib.util.spec_from_file_location(fqname, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            PLUGINS.append(mod)
            print("[Plugin] Loaded", fqname)
        except Exception as e:
            print("[Plugin load failed]", path, e)

load_plugins()

# ---------------------------------------------------------------------
# Page loader
# ---------------------------------------------------------------------
PAGES = {}

def load_pages():
    global PAGES
    PAGES = {}
    pagedir = os.path.join(os.path.dirname(__file__), "pages")
    for path in sorted(glob.glob(os.path.join(pagedir, "*.py"))):
        modname = os.path.splitext(os.path.basename(path))[0]
        if modname.startswith("__"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"pages.{modname}", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "PAGE_ID"):
                PAGES[mod.PAGE_ID] = mod
                print(f"[Page] Loaded {modname} → {mod.PAGE_ID}")
        except Exception as e:
            print("[Page load failed]", path, e)

load_pages()

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def draw_line(row, text):
    sys.stdout.write(term.move_yx(row, 0) + text[:SCREEN_COLS].ljust(SCREEN_COLS))

def plugin_state_dict():
    return {
        "tick": tick_counter,
        "bar": bar_counter,
        "running": running,
        "bpm": bpm,
        "cols": SCREEN_COLS,
        "rows": SCREEN_ROWS,
        "y_offset": 3,  # safe area: plugins/pages start drawing from here
    }

# ---------------------------------------------------------------------
# MIDI handling
# ---------------------------------------------------------------------
def handle_midi(msg: mido.Message):
    global running, tick_counter, bar_counter, bpm, last_clock_time
    if msg.type == "start":
        running = True
        tick_counter = bar_counter = 0
        clock_intervals.clear()
        last_clock_time = None
    elif msg.type == "stop":
        running = False
    elif msg.type == "clock":
        if not running:
            return
        tick_counter += 1
        if (tick_counter % (24 * 4)) == 0:
            bar_counter += 1
        now = time.time()
        if last_clock_time is not None:
            clock_intervals.append(now - last_clock_time)
            if clock_intervals:
                avg = sum(clock_intervals) / len(clock_intervals)
                bpm = 60.0 / (24 * avg)
        last_clock_time = now
    elif msg.type in ("note_on", "note_off", "control_change", "program_change"):
        # send to plugins (always)
        for mod in PLUGINS:
            if hasattr(mod, "handle"):
                try:
                    mod.handle(msg)
                except Exception:
                    pass

        # update shared polyphonic note state (always)
        polydisplay.handle(msg)

        # send to active page
        page = PAGES.get(current_page)
        if page and hasattr(page, "handle"):
            try:
                page.handle(msg)
            except Exception:
                pass

        # NEW: send to any page that opts into background handling
        for pid, pg in PAGES.items():
            if pid == current_page:
                continue
            if getattr(pg, "BACKGROUND", False) and hasattr(pg, "handle"):
                try:
                    pg.handle(msg)
                except Exception:
                    pass


# ---------------------------------------------------------------------
# UI loop
# ---------------------------------------------------------------------
exit_flag = False
current_page = 1  # Start on Notes
last_page = None
last_header = ""

def ui_loop():
    global last_page, current_page, exit_flag, last_header
    with term.fullscreen(), term.hidden_cursor():
        sys.stdout.write(term.home + term.clear)
        while not exit_flag:
            # clear on page switch
            if current_page != last_page:
                sys.stdout.write(term.home + term.clear)
                last_page = current_page

            state = plugin_state_dict()

            # --- HEADER (row 0)
            page_titles = "  ".join(
                f"[{pid}:{p.PAGE_NAME}]" for pid, p in sorted(PAGES.items())
            )
            if page_titles != last_header:
                draw_line(0, page_titles[:SCREEN_COLS])
                last_header = page_titles

            # --- TRANSPORT (row 1)
            status = "RUN" if running else "STOP"
            metronome = "●" if running and (tick_counter % 24) < 3 else "○"
            draw_line(1, f" {status:<4}  {bpm:6.1f} BPM   BAR {bar_counter:04d}   {metronome}")

            # --- CLEAR separator row 2
            draw_line(2, "")

            # --- PAGE CONTENT (start row 3)
            page = PAGES.get(current_page)
            if page and hasattr(page, "draw"):
                try:
                    page.draw(state)
                except Exception as e:
                    draw_line(3, f"[Error {current_page}] {e}")
            else:
                draw_line(3, f"No page loaded for {current_page}")

            # --- PLUGIN VISUALS (respect y_offset)
            for mod in PLUGINS:
                if hasattr(mod, "draw"):
                    try:
                        if len(signature(mod.draw).parameters) == 1:
                            mod.draw(state)
                        else:
                            mod.draw()
                    except Exception:
                        pass

            sys.stdout.flush()
            time.sleep(1.0 / FPS)

# ---------------------------------------------------------------------
# Autoconnect + keyboard listener + main
# ---------------------------------------------------------------------
def autoconnect_fixed():
    print("[AutoConnect] Attempting 20:0 → 128:0 ...")
    for attempt in range(10):
        try:
            subprocess.run(
                ["aconnect", "20:0", "128:0"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print("[AutoConnect] Connected Cirklon (20:0) → GreenCRT Monitor (128:0)")
            return
        except subprocess.CalledProcessError:
            print(f"[AutoConnect] Attempt {attempt+1}: retrying...")
            time.sleep(1)
    print("[AutoConnect] Gave up after 10 attempts.")

#keyboard
def keyboard_listener():
    global current_page, exit_flag
    with term.cbreak():
        while not exit_flag:
            key = term.inkey(timeout=0.05)
            if not key:
                continue

            # 1) page gets first shot; if it handles, skip everything else
            page = PAGES.get(current_page)
            if page and hasattr(page, "keypress"):
                try:
                    handled = page.keypress(key)
                    if handled:
                        continue
                except Exception:
                    pass

            # 2) global keys
            if key.is_sequence and key.name == "KEY_ESCAPE":
                exit_flag = True
                break
            elif key in "0123456789":
                current_page = int(key)
                continue
            elif key.lower() == "q":
                exit_flag = True
                break

# ---------- SCROLLING HANDLERS ----------
def scroll_up():
    global scroll_offset
    if scroll_offset + 1 < len(log_buffer):
        scroll_offset += 1

def scroll_down():
    global scroll_offset
    if scroll_offset > 0:
        scroll_offset -= 1

def page_up():
    global scroll_offset
    scroll_offset = min(len(log_buffer) - 1, scroll_offset + VISIBLE_ROWS_TARGET)

def page_down():
    global scroll_offset
    scroll_offset = max(0, scroll_offset - VISIBLE_ROWS_TARGET)

def scroll_home():
    global scroll_offset
    scroll_offset = len(log_buffer)

def scroll_end():
    global scroll_offset
    scroll_offset = 0




def main():
    print("[Info] Starting MIDI backend...")
    mido.set_backend("mido.backends.rtmidi")
    print("\n[Startup] Loaded plugins:")
    for mod in PLUGINS:
        print("   •", mod.__name__)
    sys.stdout.flush()
    time.sleep(2)

    try:
        with mido.open_input("GreenCRT Monitor", virtual=True) as port:
            print("[Info] Listening on 'GreenCRT Monitor'")
            autoconnect_fixed()

            threading.Thread(target=ui_loop, daemon=True).start()
            threading.Thread(target=keyboard_listener, daemon=True).start()

            while not exit_flag:
                for msg in port.iter_pending():
                    handle_midi(msg)
                time.sleep(0.001)
    except KeyboardInterrupt:
        sys.stdout.write(term.normal)
    except Exception as e:
        sys.stdout.write(term.normal)
        print("Fatal error:", e)
    finally:
        sys.stdout.write(term.normal)

if __name__ == "__main__":
    main()
