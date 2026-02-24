# -*- coding: utf-8 -*-
# midicrt.py — CRT-style MIDI monitor / visualizer for Cirklon

import os, sys, time, glob, importlib.util, subprocess, threading, re
from configutil import load_section, save_section
from inspect import signature
from blessed import Terminal
import mido
from engine.core import MidiEngine
from engine.ipc import SnapshotPublisher
from ui.model import Frame
from ui.renderers.text import TextRenderer

# Ensure the running script is importable as `midicrt` so plugin/page imports do
# not re-execute this module under a different name.
sys.modules.setdefault("midicrt", sys.modules[__name__])

term = Terminal()
text_renderer = TextRenderer(term)
AUTOCONNECT_LOG = []
PANIC_OUT_PORT = None
PANIC_OUT_VIRTUAL = False
PANIC_AUTOCONNECT_DONE = False
PANIC_OUTPUT_NAME = os.environ.get("MIDICRT_PANIC_NAME", "GreenCRT Panic")
PANIC_DST_HINTS = [
    h.strip() for h in os.environ.get(
        "MIDICRT_PANIC_DST",
        "USB MIDI Interface,USB MIDI,MIDI 1",
    ).split(",") if h.strip()
]

_panic_cfg = load_section("panic")
if _panic_cfg is None:
    _panic_cfg = {}
try:
    PANIC_OUTPUT_NAME = str(_panic_cfg.get("output_name", PANIC_OUTPUT_NAME))
    hints = _panic_cfg.get("dst_hints", PANIC_DST_HINTS)
    if isinstance(hints, str):
        PANIC_DST_HINTS = [h.strip() for h in hints.split(",") if h.strip()]
    elif isinstance(hints, list):
        PANIC_DST_HINTS = [str(h).strip() for h in hints if str(h).strip()]
except Exception:
    pass

# Explicitly import shared poly display so it's never double-loaded
import plugins.polydisplay as polydisplay

# ---------------------------------------------------------------------
# Display / timing
# ---------------------------------------------------------------------
SCREEN_COLS = getattr(term, 'width', 95) or 95
SCREEN_ROWS = getattr(term, 'height', 30) or 30
FPS = 60.0
HEADER_SCROLL_SPEED = 4.0  # characters per second; set to 0 to disable scrolling

_core_cfg = load_section("core")
if _core_cfg is None:
    _core_cfg = {}
IPC_ENABLED = True
IPC_SOCKET_PATH = "/tmp/midicrt.sock"
IPC_PUBLISH_HZ = 20.0
try:
    FPS = float(_core_cfg.get("fps", FPS))
    HEADER_SCROLL_SPEED = float(_core_cfg.get("header_scroll_speed", HEADER_SCROLL_SPEED))
    _ipc_cfg = _core_cfg.get("ipc", {}) if isinstance(_core_cfg.get("ipc", {}), dict) else {}
    IPC_ENABLED = bool(_ipc_cfg.get("enabled", IPC_ENABLED))
    IPC_SOCKET_PATH = str(_ipc_cfg.get("socket_path", IPC_SOCKET_PATH))
    IPC_PUBLISH_HZ = float(_ipc_cfg.get("publish_hz", IPC_PUBLISH_HZ))
except Exception:
    pass
try:
    save_section("core", {
        "fps": float(FPS),
        "header_scroll_speed": float(HEADER_SCROLL_SPEED),
        "ipc": {
            "enabled": bool(IPC_ENABLED),
            "socket_path": str(IPC_SOCKET_PATH),
            "publish_hz": float(IPC_PUBLISH_HZ),
        },
    })
except Exception:
    pass

try:
    save_section("panic", {
        "output_name": str(PANIC_OUTPUT_NAME),
        "dst_hints": list(PANIC_DST_HINTS),
    })
except Exception:
    pass

# ---------------------------------------------------------------------
# Helpers (exposed early so pages can import them safely)
# ---------------------------------------------------------------------
def draw_line(row, text):
    sys.stdout.write(term.move_yx(row, 0) + text[:SCREEN_COLS].ljust(SCREEN_COLS))


def plugin_state_dict():
    if ENGINE:
        return ENGINE.make_plugin_state(SCREEN_COLS, SCREEN_ROWS, y_offset=3)
    return {
        "tick": tick_counter,
        "bar": bar_counter,
        "running": running,
        "bpm": bpm,
        "cols": SCREEN_COLS,
        "rows": SCREEN_ROWS,
        "y_offset": 3,
    }

# ---------------------------------------------------------------------
# Transport state
# ---------------------------------------------------------------------
bpm = 0.0
tick_counter = 0
bar_counter = 0
running = False
ENGINE = None

# ---------------------------------------------------------------------
# Instrument names
# ---------------------------------------------------------------------
def load_instrument_names():
    from configutil import load_section, save_section
    names = []
    # 1) Try shared config
    try:
        section = load_section("instruments")
        if section and isinstance(section.get("names"), list):
            names = [str(n).strip() for n in section.get("names", []) if str(n).strip()]
    except Exception:
        names = []
    # 2) Legacy fallback: instruments.txt
    if not names:
        path = os.path.join(os.path.dirname(__file__), "instruments.txt")
        try:
            with open(path, "r", encoding="utf-8") as f:
                names = [ln.strip() for ln in f if ln.strip()]
        except FileNotFoundError:
            names = []
    try:
        # normalize/pad to 16 and save back to shared config
        while len(names) < 16:
            names.append(f"Channel {len(names)+1}")
        names = names[:16]
        save_section("instruments", {"names": names})
    except Exception:
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
            # register in sys.modules to share state across imports
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            PLUGINS.append(mod)
            print("[Plugin] Loaded", fqname)
        except Exception as e:
            print("[Plugin load failed]", path, e)

load_plugins()
try:
    import plugins.zharmony as zharmony  # ensure harmony plugin is registered
    if zharmony not in PLUGINS:
        PLUGINS.append(zharmony)
        print("[Plugin] Loaded plugins.zharmony (forced)")
except Exception:
    pass

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
            # register in sys.modules to support cross-page imports
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            if hasattr(mod, "PAGE_ID"):
                PAGES[mod.PAGE_ID] = mod
                print(f"[Page] Loaded {modname} → {mod.PAGE_ID}")
        except Exception as e:
            print("[Page load failed]", path, e)

load_pages()

# ---------------------------------------------------------------------
# MIDI handling
# ---------------------------------------------------------------------
def _sync_transport_globals(snapshot):
    global running, tick_counter, bar_counter, bpm
    running = snapshot["running"]
    tick_counter = snapshot["tick_counter"]
    bar_counter = snapshot["bar_counter"]
    bpm = snapshot["bpm"]


def handle_engine_event(event, msg: mido.Message):
    # wake screensaver on note/CC/prog activity (mirrors keypress path)
    if event["kind"] in ("note_on", "note_off", "control_change", "program_change"):
        _ss = next((m for m in PLUGINS if hasattr(m, "is_active") and hasattr(m, "deactivate")), None)
        if _ss and _ss.is_active():
            _ss.deactivate()
        polydisplay.handle(msg)

    _sync_transport_globals(ENGINE.get_snapshot())


# ---------------------------------------------------------------------
# Shared status slots (written by plugins, read by footer renderers)
# ---------------------------------------------------------------------
sysex_status = ""       # last sysex command summary, displayed in footer
sysex_status_time = 0.0

# ---------------------------------------------------------------------
# UI loop
# ---------------------------------------------------------------------
exit_flag = False
current_page = 1  # Start on Notes
last_page = None
last_header = ""
_header_scroll_offset = 0.0
_header_scroll_last_time = 0.0
_auto_scroll_offset = 0.0
_auto_scroll_last_time = 0.0
_auto_last_msg = ""
_auto_last_window = 0


def switch_page(page):
    """Set current page if it exists. Returns (ok, resolved_page)."""
    global current_page
    try:
        page_id = int(page)
    except (TypeError, ValueError):
        return False, None
    if page_id not in PAGES:
        return False, page_id
    current_page = page_id
    return True, page_id

SNAPSHOT_PUBLISHER = SnapshotPublisher(
    socket_path=IPC_SOCKET_PATH,
    enabled=IPC_ENABLED,
    publish_hz=IPC_PUBLISH_HZ,
)
SNAPSHOT_PUBLISHER.start()

ENGINE = MidiEngine(
    plugins=PLUGINS,
    pages=PAGES,
    get_current_page=lambda: current_page,
    on_event=handle_engine_event,
    publisher=SNAPSHOT_PUBLISHER,
)

def ui_loop():
    global last_page, current_page, exit_flag, last_header, SCREEN_COLS, SCREEN_ROWS
    global _header_scroll_offset, _header_scroll_last_time
    global _auto_scroll_offset, _auto_scroll_last_time, _auto_last_msg, _auto_last_window
    with term.fullscreen(), term.hidden_cursor():
        sys.stdout.write(term.home + term.clear)
        while not exit_flag:
            # refresh screen size each frame so pages/plugins can use all space
            try:
                w = getattr(term, 'width', SCREEN_COLS) or SCREEN_COLS
                h = getattr(term, 'height', SCREEN_ROWS) or SCREEN_ROWS
                # keep within sensible bounds
                if w != SCREEN_COLS or h != SCREEN_ROWS:
                    SCREEN_COLS = w
                    SCREEN_ROWS = h
                    # force header redraw on resize
                    last_header = ""
            except Exception:
                pass
            # clear on page switch
            if current_page != last_page:
                sys.stdout.write(term.home + term.clear)
                last_page = current_page
                last_header = ""  # force header redraw after clear

            snapshot = ENGINE.get_snapshot() if ENGINE else {
                "tick_counter": tick_counter,
                "bar_counter": bar_counter,
                "running": running,
                "bpm": bpm,
            }
            state = plugin_state_dict()

            # --- HEADER (row 0) — scrolling marquee when wider than screen
            page_titles = "  ".join(
                f"[{pid}:{p.PAGE_NAME}]" for pid, p in sorted(PAGES.items())
            )
            _now = time.time()
            if page_titles != last_header:
                last_header = page_titles
                _header_scroll_offset = 0.0
                _header_scroll_last_time = _now
            if len(page_titles) <= SCREEN_COLS:
                draw_line(0, page_titles)
            else:
                dt = _now - _header_scroll_last_time
                _header_scroll_offset += HEADER_SCROLL_SPEED * dt
                _header_scroll_last_time = _now
                sep = "    "
                full = page_titles + sep
                offset = int(_header_scroll_offset) % len(full)
                draw_line(0, (full * 2)[offset:offset + SCREEN_COLS])

            # --- TRANSPORT (row 1)
            status = "RUN" if snapshot["running"] else "STOP"
            metronome = "●" if snapshot["running"] and (snapshot["tick_counter"] % 24) < 3 else "○"
            base = f" {status:<4}  {snapshot['bpm']:6.1f} BPM   BAR {snapshot['bar_counter']:04d}   {metronome}"
            msg = AUTOCONNECT_LOG[-1] if AUTOCONNECT_LOG else ""
            if msg:
                msg = msg.strip()
                max_avail = max(1, SCREEN_COLS - 1)
                if len(msg) <= 12:
                    window = min(len(msg), max_avail)
                else:
                    window = min(max_avail, max(8, len(msg) // 2))
                if window < 1:
                    draw_line(1, base)
                else:
                    if msg != _auto_last_msg or window != _auto_last_window:
                        _auto_last_msg = msg
                        _auto_last_window = window
                        _auto_scroll_offset = 0.0
                        _auto_scroll_last_time = _now
                    if len(msg) <= window:
                        win_text = msg.ljust(window)
                    else:
                        dt = _now - _auto_scroll_last_time
                        _auto_scroll_offset += HEADER_SCROLL_SPEED * dt
                        _auto_scroll_last_time = _now
                        sep = "    "
                        full = msg + sep
                        offset = int(_auto_scroll_offset) % len(full)
                        win_text = (full * 2)[offset:offset + window]
                    left_space = max(0, SCREEN_COLS - window - 1)
                    left = base[:left_space].ljust(left_space)
                    line = left + " " + win_text
                    draw_line(1, line)
            else:
                draw_line(1, base)

            # --- STATUS (row 2): unused now; keep blank for space
            draw_line(2, "")

            # --- PAGE CONTENT (start row 3)
            page = PAGES.get(current_page)
            if page and hasattr(page, "build_widget"):
                try:
                    content_rows = max(0, SCREEN_ROWS - 3)
                    widget = page.build_widget(state)
                    rendered = text_renderer.render(widget, Frame(cols=SCREEN_COLS, rows=content_rows))
                    for idx, line in enumerate(rendered):
                        draw_line(3 + idx, line)
                except Exception as e:
                    draw_line(3, f"[Error {current_page}] {e}")
            elif page and hasattr(page, "draw"):
                try:
                    page.draw(state)
                except Exception as e:
                    draw_line(3, f"[Error {current_page}] {e}")
            else:
                draw_line(3, f"No page loaded for {current_page}")

            # --- PLUGIN VISUALS (respect y_offset)
            if not os.environ.get("MIDICRT_DISABLE_PLUGIN_DRAW"):
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
_client_re = re.compile(r"^client\s+(\d+):\s+'([^']+)'")
_port_re = re.compile(r"^\s+(\d+)\s+'([^']+)'")


def _log_autoconnect(msg: str):
    AUTOCONNECT_LOG.append(msg)
    if len(AUTOCONNECT_LOG) > 32:
        del AUTOCONNECT_LOG[0]


def _parse_aconnect(flag: str):
    """Return list of (client_id, client_name, port_id, port_name)."""
    try:
        result = subprocess.run(
            ["aconnect", flag],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        _log_autoconnect(f"[AutoConnect] Unable to run aconnect {flag}: {exc}")
        return []

    entries = []
    current = None
    for line in result.stdout.splitlines():
        m_client = _client_re.match(line)
        if m_client:
            current = (m_client.group(1), m_client.group(2))
            continue
        if current:
            m_port = _port_re.match(line)
            if m_port:
                entries.append(
                    (
                        current[0],
                        current[1],
                        m_port.group(1),
                        m_port.group(2),
                    )
                )
    return entries


def _find_matching_port(flag: str, hints):
    hints = [h.strip() for h in hints if h.strip()]
    if not hints:
        return None
    entries = _parse_aconnect(flag)
    best = None
    for hint in hints:
        hint_lower = hint.lower()
        for client_id, client_name, port_id, port_name in entries:
            if hint_lower in client_name.lower() or hint_lower in port_name.lower():
                candidate = (
                    int(client_id),
                    int(port_id),
                    f"{client_id}:{port_id}",
                    client_name,
                    port_name,
                )
                if not best or candidate > best:
                    best = candidate
    if best:
        return best[2], best[3], best[4]
    return None


def autoconnect_dynamic(target_port_name: str):
    """Attempt to connect Cirklon → this virtual port with heuristics."""
    src_hints = [
        h.strip()
        for h in os.environ.get(
            "MIDICRT_AUTOCONNECT_SRC",
            "Cirklon,Cirklon MIDI,Cirklon Seq",
        ).split(",")
        if h.strip()
    ]
    dst_hints = [target_port_name] + [
        h.strip()
        for h in os.environ.get("MIDICRT_AUTOCONNECT_DST", target_port_name).split(",")
        if h.strip()
    ]

    outputs = _parse_aconnect("-o")
    inputs = _parse_aconnect("-i")

    def match_ports(entries, hints):
        matches = []
        for client_id, client_name, port_id, port_name in entries:
            text = f"{client_name} {port_name}".lower()
            for hint in hints:
                if hint.lower() in text:
                    matches.append((f"{client_id}:{port_id}", client_name, port_name))
                    break
        return matches

    src_matches = match_ports(outputs, src_hints)
    dst_matches = match_ports(inputs, dst_hints)

    forced_pairs = []
    forced_env = os.environ.get("MIDICRT_AUTOCONNECT_FORCE", "")
    if forced_env:
        for part in forced_env.split(","):
            if "->" in part:
                lhs, rhs = part.split("->", 1)
                forced_pairs.append((lhs.strip(), rhs.strip()))

    if not src_matches:
        _log_autoconnect(f"[AutoConnect] Could not locate Cirklon output; hints: {src_hints}")
    if not dst_matches:
        _log_autoconnect(f"[AutoConnect] Could not locate monitor input; hints: {dst_hints}")

    if not src_matches or not dst_matches:
        if outputs:
            _log_autoconnect("[AutoConnect] Available outputs:")
            for client_id, client_name, port_id, port_name in outputs:
                _log_autoconnect(f"   {client_id}:{port_id}  {client_name} — {port_name}")
        if inputs:
            _log_autoconnect("[AutoConnect] Available inputs:")
            for client_id, client_name, port_id, port_name in inputs:
                _log_autoconnect(f"   {client_id}:{port_id}  {client_name} — {port_name}")

    # Some RtMidi virtual ports only appear under -o; include them if needed.
    if not dst_matches:
        extra_dst = match_ports(outputs, dst_hints)
        for entry in extra_dst:
            if entry not in dst_matches:
                dst_matches.append(entry)
    for client_id, client_name, port_id, port_name in outputs:
        if "rtmidi" in client_name.lower() or "greencrt" in client_name.lower():
            entry = (f"{client_id}:{port_id}", client_name, port_name)
            if entry not in dst_matches:
                dst_matches.append(entry)

    # Fallback guesses
    fallback_sources = ["20:0"]
    fallback_dests = ["128:0", "129:0", "130:0", "131:0"]

    src_candidates = src_matches + [(fs, "Fallback", "Cirklon guess") for fs in fallback_sources]
    dst_candidates = dst_matches + [(fd, "Fallback", "Monitor guess") for fd in fallback_dests]

    # prepend forced pairs so they are attempted first
    for src_id, dst_id in forced_pairs:
        src_candidates.insert(0, (src_id, "Forced", "Source override"))
        dst_candidates.insert(0, (dst_id, "Forced", "Destination override"))

    def dedupe(seq):
        seen = set()
        result = []
        for item in seq:
            if item[0] in seen:
                continue
            seen.add(item[0])
            result.append(item)
        return result

    src_candidates = dedupe(src_candidates)
    dst_candidates = dedupe(dst_candidates)

    attempted = set()

    # If explicit forced pairs were supplied, try them first.
    for src_id, dst_id in forced_pairs:
        if _connect_pair(src_id, dst_id):
            return
        attempted.add((src_id, dst_id))

    for src_id, src_client, src_port in src_candidates:
        for dst_id, dst_client, dst_port in dst_candidates:
            key = (src_id, dst_id)
            if key in attempted:
                continue
            attempted.add(key)
            _log_autoconnect(
                f"[AutoConnect] Trying {src_client}:{src_port} ({src_id}) → {dst_client}:{dst_port} ({dst_id})"
            )
            if _connect_pair(src_id, dst_id):
                return

    _log_autoconnect("[AutoConnect] Exhausted attempts; please connect manually.")


def autoconnect_panic_output():
    """Attempt to connect panic output port to USB MIDI out."""
    global PANIC_AUTOCONNECT_DONE
    if PANIC_AUTOCONNECT_DONE or not PANIC_OUT_VIRTUAL:
        return

    src_hints = [PANIC_OUTPUT_NAME]
    dst_hints = PANIC_DST_HINTS

    outs = _parse_aconnect("-o")
    ins = _parse_aconnect("-i")

    def match_port(entries, hints):
        for hint in hints:
            h = hint.lower()
            for client_id, client_name, port_id, port_name in entries:
                text = f"{client_name} {port_name}".lower()
                if h in text:
                    return f"{client_id}:{port_id}", client_name, port_name
        return None, None, None

    src_id, src_client, src_port = match_port(outs, src_hints)
    dst_id, dst_client, dst_port = match_port(ins, dst_hints)

    if not src_id or not dst_id:
        _log_autoconnect(f"[Panic] Could not locate ports (src={src_id}, dst={dst_id})")
        return

    try:
        subprocess.run(["aconnect", src_id, dst_id], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _log_autoconnect(f"[Panic] Connected {src_client}:{src_port} ({src_id}) → {dst_client}:{dst_port} ({dst_id})")
        PANIC_AUTOCONNECT_DONE = True
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.decode("utf-8", errors="ignore").strip()
        if not err:
            err = str(exc)
        _log_autoconnect(f"[Panic] Connect failed for {src_id} → {dst_id}: {err}")


def _connect_pair(src_id: str, dst_id: str) -> bool:
    for attempt in range(5):
        try:
            subprocess.run(
                ["aconnect", src_id, dst_id],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            _log_autoconnect(f"[AutoConnect] Connected {src_id} → {dst_id}.")
            return True
        except subprocess.CalledProcessError as exc:
            err = exc.stderr.decode("utf-8", errors="ignore").strip()
            if not err:
                err = str(exc)
            _log_autoconnect(
                f"[AutoConnect] Attempt {attempt + 1} failed for {src_id} → {dst_id}: {err}"
            )
            time.sleep(0.5)
    return False

#keyboard
def keyboard_listener():
    global exit_flag
    # find plugins of interest once at startup
    _ss = next((m for m in PLUGINS if hasattr(m, "is_active") and hasattr(m, "deactivate")), None)
    _pc = next((m for m in PLUGINS if hasattr(m, "notify_keypress")), None)
    with term.cbreak():
        while not exit_flag:
            key = term.inkey(timeout=0.05)
            if not key:
                continue

            # wake from screensaver; swallow the keypress
            if _ss and _ss.is_active():
                _ss.deactivate()
                continue

            # notify page cycler of user activity
            if _pc:
                _pc.notify_keypress()

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
                switch_page(key)
                continue
            elif key == "!":
                switch_page(11)
                continue
            elif key == "@":
                switch_page(12)
                continue
            elif key == "#":
                switch_page(13)
                continue
            elif key == "$":
                switch_page(14)
                continue
            elif key == "%":
                switch_page(15)
                continue
            elif key.lower() == "t":
                switch_page(10)
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
    global PANIC_OUT_PORT
    global PANIC_OUT_VIRTUAL
    # Create panic output early so we can autoconnect it on startup
    try:
        # Prefer a real hardware output that matches hints
        panic_target = None
        for name in mido.get_output_names():
            text = name.lower()
            if any(h.lower() in text for h in PANIC_DST_HINTS):
                panic_target = name
                break
        if panic_target:
            PANIC_OUT_PORT = mido.open_output(panic_target)
            PANIC_OUT_VIRTUAL = False
            _log_autoconnect(f"[Panic] Using output {panic_target}")
        else:
            # Fall back to a virtual port and try to aconnect it
            try:
                PANIC_OUT_PORT = mido.open_output(PANIC_OUTPUT_NAME)
            except (IOError, OSError):
                PANIC_OUT_PORT = mido.open_output(PANIC_OUTPUT_NAME, virtual=True)
            PANIC_OUT_VIRTUAL = True
    except Exception:
        PANIC_OUT_PORT = None
        PANIC_OUT_VIRTUAL = False
    print("\n[Startup] Loaded plugins:")
    for mod in PLUGINS:
        print("   •", mod.__name__)
    sys.stdout.flush()
    time.sleep(2)

    try:
        with mido.open_input("GreenCRT Monitor", virtual=True) as port:
            print("[Info] Listening on 'GreenCRT Monitor'")
            time.sleep(0.25)  # give ALSA time to register the new virtual ports
            try:
                target_name = getattr(port, "name", "GreenCRT Monitor")
            except Exception:
                target_name = "GreenCRT Monitor"
            autoconnect_dynamic(target_name)
            autoconnect_panic_output()

            threading.Thread(target=ui_loop, daemon=True).start()
            threading.Thread(target=keyboard_listener, daemon=True).start()

            ENGINE.run_input_loop(port, lambda: exit_flag)
    except KeyboardInterrupt:
        sys.stdout.write(term.normal)
    except Exception as e:
        sys.stdout.write(term.normal)
        print("Fatal error:", e)
    finally:
        try:
            SNAPSHOT_PUBLISHER.stop()
        except Exception:
            pass
        sys.stdout.write(term.normal)

if __name__ == "__main__":
    main()
