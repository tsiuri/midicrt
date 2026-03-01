# -*- coding: utf-8 -*-
# midicrt.py — CRT-style MIDI monitor / visualizer for Cirklon

import os, sys, time, glob, importlib.util, subprocess, threading, re, argparse, json
from collections import deque
from configutil import load_section, save_section
from inspect import signature
from blessed import Terminal
import mido
from engine.core import MidiEngine
from engine.ipc import SnapshotPublisher
from engine.modules import LegacyPluginModule, PianoRollViewModule
from engine.modules.interfaces import ScreenSaverModule, UserActivityModule
from engine.adapters.aconnect_parser import parse_aconnect_output
from ui.model import Frame
from ui.composition import build_footer_widget, build_transport_widget
from ui.overlays import capture_plugin_overlay_widget, compose_overlay_rows
from ui.renderers.text import TextRenderer
from engine.page_contracts import capture_legacy_page_view
from ui.view_contracts import widget_from_page_view
from ui.model import Line, TextBlock

# Ensure the running script is importable as `midicrt` so plugin/page imports do
# not re-execute this module under a different name.
sys.modules.setdefault("midicrt", sys.modules[__name__])


class _LegacyLineCapture:
    def __init__(self, cols: int, rows: int):
        self.cols = max(1, int(cols))
        self.rows = max(1, int(rows))
        self.lines = ["" for _ in range(self.rows)]

    def draw_line(self, y: int, text: str) -> None:
        y_int = int(y)
        if 0 <= y_int < self.rows:
            self.lines[y_int] = str(text)[: self.cols]


def _widget_from_legacy_draw(draw_fn, state, draw_line_ref):
    cols = int(state.get("cols", 100))
    rows = int(state.get("rows", 30))
    y_offset = int(state.get("y_offset", 0))
    capture = _LegacyLineCapture(cols=cols, rows=rows)
    module_globals = draw_fn.__globals__
    old_draw_line = module_globals.get("draw_line", draw_line_ref)
    module_globals["draw_line"] = capture.draw_line
    try:
        draw_fn(state)
    finally:
        module_globals["draw_line"] = old_draw_line
    return TextBlock(lines=[Line.plain(text) for text in capture.lines[y_offset:]])

term = Terminal()
text_renderer = TextRenderer(term)
_ui_line_renderer = TextRenderer(term)
ACTIVE_PROFILE = "run_tui"
ACTIVE_RENDER_BACKEND = "text"
_compositor = None   # set to CompositorRenderer when profile=run_compositor
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
PANIC_RETRY_ENABLE = True
PANIC_RETRY_INTERVAL = 1.0
PANIC_RETRY_INTERVAL_CAP = 30.0


def _append_startup_log(message: str):
    log_path = os.path.join(os.path.dirname(__file__), "log.txt")
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [startup] {message}\n")
    except Exception:
        pass


def configure_startup_profile(profile: str):
    """Select runtime profile + renderer backend.

    run_tui:        default terminal-safe profile.
    run_pixel:      optional SDL/pygame profile (gated behind env flag).
    run_compositor: direct /dev/fb0 pixel rendering via PIL compositor.
    """
    global ACTIVE_PROFILE, ACTIVE_RENDER_BACKEND, text_renderer, _compositor

    selected = profile or "run_tui"
    ACTIVE_PROFILE = selected

    if selected == "run_compositor":
        try:
            from fb.compositor_renderer import CompositorRenderer
            cr = CompositorRenderer()
            text_renderer = cr
            _compositor = cr
            ACTIVE_RENDER_BACKEND = "compositor"
            global FPS
            FPS = 60.0   # target full refresh; may drop if workload exceeds budget
        except Exception as exc:
            ACTIVE_RENDER_BACKEND = "text(fallback)"
            _compositor = None
            _append_startup_log(
                f"profile=run_compositor unavailable ({exc}); falling back to text"
            )
            ACTIVE_PROFILE = "run_tui"
    elif selected == "run_pixel":
        feature_enabled = os.environ.get("MIDICRT_ENABLE_PIXEL", "0").strip().lower() in {"1", "true", "yes", "on"}
        if feature_enabled:
            renderer_name = os.environ.get("MIDICRT_PIXEL_RENDERER", "sdl2")
            try:
                # Optional import path; keep GUI deps out of TUI startup.
                from ui.renderers.pixel import PixelRenderer

                text_renderer = PixelRenderer(renderer_name=renderer_name)
                ACTIVE_RENDER_BACKEND = f"pixel:{renderer_name}"
            except Exception as exc:
                ACTIVE_RENDER_BACKEND = "text(fallback)"
                _append_startup_log(
                    f"profile=run_pixel requested but unavailable ({exc}); falling back to text"
                )
        else:
            ACTIVE_RENDER_BACKEND = "text(fallback)"
            _append_startup_log(
                "profile=run_pixel requested without MIDICRT_ENABLE_PIXEL=1; falling back to text"
            )
    else:
        ACTIVE_PROFILE = "run_tui"
        ACTIVE_RENDER_BACKEND = "text"

    _append_startup_log(f"profile={ACTIVE_PROFILE} backend={ACTIVE_RENDER_BACKEND}")

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
    PANIC_RETRY_ENABLE = bool(_panic_cfg.get("retry_enable", PANIC_RETRY_ENABLE))
    PANIC_RETRY_INTERVAL = float(_panic_cfg.get("retry_interval", PANIC_RETRY_INTERVAL))
    PANIC_RETRY_INTERVAL_CAP = float(_panic_cfg.get("retry_interval_cap", PANIC_RETRY_INTERVAL_CAP))
    if PANIC_RETRY_INTERVAL <= 0.0:
        PANIC_RETRY_INTERVAL = 1.0
    if PANIC_RETRY_INTERVAL_CAP < PANIC_RETRY_INTERVAL:
        PANIC_RETRY_INTERVAL_CAP = PANIC_RETRY_INTERVAL
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


def _safe_float(value, default):
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value, default):
    try:
        return int(value)
    except Exception:
        return int(default)


class RuntimeBudgetPolicy:
    """Small deterministic runtime budget governor for low-power hardware."""

    def __init__(self, target_fps: float, cfg: dict | None):
        cfg = cfg if isinstance(cfg, dict) else {}
        self.enabled = bool(cfg.get("enabled", True))
        self.window = max(16, _safe_int(cfg.get("window_frames", 90), 90))
        self.sustain_windows = max(1, _safe_int(cfg.get("sustain_windows", 3), 3))
        self.relax_windows = max(1, _safe_int(cfg.get("relax_windows", 4), 4))
        self.over_budget_ratio = min(1.0, max(0.05, _safe_float(cfg.get("over_budget_ratio", 0.35), 0.35)))
        self.target_fps = max(5.0, _safe_float(target_fps, 30.0))
        self.target_dt = 1.0 / self.target_fps
        self.frame_dt = deque(maxlen=self.window)
        self.over_budget_flags = deque(maxlen=self.window)
        self.level = 0
        self._over_streak = 0
        self._under_streak = 0

    def note_frame(self, frame_dt: float):
        dt = max(0.0, _safe_float(frame_dt, self.target_dt))
        self.frame_dt.append(dt)
        self.over_budget_flags.append(1 if dt > self.target_dt else 0)
        if len(self.over_budget_flags) < self.window:
            return
        ratio = self.over_budget_ratio_value()
        if ratio >= self.over_budget_ratio:
            self._over_streak += 1
            self._under_streak = 0
        elif ratio <= (self.over_budget_ratio * 0.5):
            self._under_streak += 1
            self._over_streak = 0
        else:
            self._over_streak = 0
            self._under_streak = 0

    def maybe_step(self) -> str | None:
        if not self.enabled:
            return None
        if self._over_streak >= self.sustain_windows and self.level < 2:
            self.level += 1
            self._over_streak = 0
            self._under_streak = 0
            return "down"
        if self._under_streak >= self.relax_windows and self.level > 0:
            self.level -= 1
            self._over_streak = 0
            self._under_streak = 0
            return "up"
        return None

    def avg_frame_ms(self) -> float:
        if not self.frame_dt:
            return 0.0
        return (sum(self.frame_dt) / len(self.frame_dt)) * 1000.0

    def over_budget_ratio_value(self) -> float:
        if not self.over_budget_flags:
            return 0.0
        return float(sum(self.over_budget_flags)) / float(len(self.over_budget_flags))

_core_cfg = load_section("core")
if _core_cfg is None:
    _core_cfg = {}
IPC_ENABLED = True
IPC_SOCKET_PATH = "/tmp/midicrt.sock"
IPC_PUBLISH_HZ = 20.0
MODULE_OVERLOAD_COST_MS = 6.0
MODULE_POLICIES = {}
AUTOCONNECT_FALLBACK_SOURCES = []
AUTOCONNECT_FALLBACK_DESTINATIONS = []
TEMPO_METRICS_CFG = {"interval_window": 24, "baseline_window": 96, "stats_window": 24}
CORE_FEATURE_FLAGS = {"contract_page_views": False, "contract_legacy_event_router": True}
RUNTIME_POLICY_CFG = {
    "enabled": True,
    "window_frames": 90,
    "sustain_windows": 3,
    "relax_windows": 4,
    "over_budget_ratio": 0.35,
    "page_cache_ttl_ms": 200,
    "notes_page_cache_hz": 30,
    "notes_badge_hz": 24,
    "plugin_overlay_hz": 10,
    "degrade_steps": [
        {
            "page_cache_ttl_ms": 260,
            "notes_page_cache_hz": 24,
            "notes_badge_hz": 18,
            "plugin_overlay_hz": 8,
        },
        {
            "page_cache_ttl_ms": 333,
            "notes_page_cache_hz": 18,
            "notes_badge_hz": 12,
            "plugin_overlay_hz": 6,
        },
    ],
}
try:
    FPS = float(_core_cfg.get("fps", FPS))
    HEADER_SCROLL_SPEED = float(_core_cfg.get("header_scroll_speed", HEADER_SCROLL_SPEED))
    _ipc_cfg = _core_cfg.get("ipc", {}) if isinstance(_core_cfg.get("ipc", {}), dict) else {}
    IPC_ENABLED = bool(_ipc_cfg.get("enabled", IPC_ENABLED))
    IPC_SOCKET_PATH = str(_ipc_cfg.get("socket_path", IPC_SOCKET_PATH))
    IPC_PUBLISH_HZ = float(_ipc_cfg.get("publish_hz", IPC_PUBLISH_HZ))
    _mod_cfg = _core_cfg.get("module_scheduler", {}) if isinstance(_core_cfg.get("module_scheduler", {}), dict) else {}
    MODULE_OVERLOAD_COST_MS = float(_mod_cfg.get("overload_cost_ms", MODULE_OVERLOAD_COST_MS))
    MODULE_POLICIES = _mod_cfg.get("modules", {}) if isinstance(_mod_cfg.get("modules", {}), dict) else {}
    _autoc_cfg = _core_cfg.get("autoconnect", {}) if isinstance(_core_cfg.get("autoconnect", {}), dict) else {}
    _fallback_sources = _autoc_cfg.get("fallback_sources", [])
    _fallback_dests = _autoc_cfg.get("fallback_destinations", [])
    if isinstance(_fallback_sources, list):
        AUTOCONNECT_FALLBACK_SOURCES = [str(v).strip() for v in _fallback_sources if str(v).strip()]
    if isinstance(_fallback_dests, list):
        AUTOCONNECT_FALLBACK_DESTINATIONS = [str(v).strip() for v in _fallback_dests if str(v).strip()]
    _tempo_metrics_cfg = _core_cfg.get("tempo_metrics", {}) if isinstance(_core_cfg.get("tempo_metrics", {}), dict) else {}
    _feature_flags_cfg = _core_cfg.get("feature_flags", {}) if isinstance(_core_cfg.get("feature_flags", {}), dict) else {}
    _runtime_policy_cfg = _core_cfg.get("runtime_policy", {}) if isinstance(_core_cfg.get("runtime_policy", {}), dict) else {}
    CORE_FEATURE_FLAGS["contract_page_views"] = bool(_feature_flags_cfg.get("contract_page_views", CORE_FEATURE_FLAGS["contract_page_views"]))
    CORE_FEATURE_FLAGS["contract_legacy_event_router"] = bool(_feature_flags_cfg.get("contract_legacy_event_router", CORE_FEATURE_FLAGS["contract_legacy_event_router"]))
    RUNTIME_POLICY_CFG["enabled"] = bool(_runtime_policy_cfg.get("enabled", RUNTIME_POLICY_CFG["enabled"]))
    RUNTIME_POLICY_CFG["window_frames"] = max(16, int(_runtime_policy_cfg.get("window_frames", RUNTIME_POLICY_CFG["window_frames"])))
    RUNTIME_POLICY_CFG["sustain_windows"] = max(1, int(_runtime_policy_cfg.get("sustain_windows", RUNTIME_POLICY_CFG["sustain_windows"])))
    RUNTIME_POLICY_CFG["relax_windows"] = max(1, int(_runtime_policy_cfg.get("relax_windows", RUNTIME_POLICY_CFG["relax_windows"])))
    RUNTIME_POLICY_CFG["over_budget_ratio"] = min(1.0, max(0.05, float(_runtime_policy_cfg.get("over_budget_ratio", RUNTIME_POLICY_CFG["over_budget_ratio"]))))
    RUNTIME_POLICY_CFG["page_cache_ttl_ms"] = max(50, int(_runtime_policy_cfg.get("page_cache_ttl_ms", RUNTIME_POLICY_CFG["page_cache_ttl_ms"])))
    RUNTIME_POLICY_CFG["notes_page_cache_hz"] = max(2, float(_runtime_policy_cfg.get("notes_page_cache_hz", RUNTIME_POLICY_CFG["notes_page_cache_hz"])))
    RUNTIME_POLICY_CFG["notes_badge_hz"] = max(2, float(_runtime_policy_cfg.get("notes_badge_hz", RUNTIME_POLICY_CFG["notes_badge_hz"])))
    RUNTIME_POLICY_CFG["plugin_overlay_hz"] = max(2, float(_runtime_policy_cfg.get("plugin_overlay_hz", RUNTIME_POLICY_CFG["plugin_overlay_hz"])))
    _steps = _runtime_policy_cfg.get("degrade_steps", RUNTIME_POLICY_CFG["degrade_steps"])
    if isinstance(_steps, list) and _steps:
        cleaned_steps = []
        for s in _steps[:4]:
            if not isinstance(s, dict):
                continue
            cleaned_steps.append({
                "page_cache_ttl_ms": max(50, int(s.get("page_cache_ttl_ms", RUNTIME_POLICY_CFG["page_cache_ttl_ms"]))),
                "notes_page_cache_hz": max(2, float(s.get("notes_page_cache_hz", RUNTIME_POLICY_CFG["notes_page_cache_hz"]))),
                "notes_badge_hz": max(2, float(s.get("notes_badge_hz", RUNTIME_POLICY_CFG["notes_badge_hz"]))),
                "plugin_overlay_hz": max(2, float(s.get("plugin_overlay_hz", RUNTIME_POLICY_CFG["plugin_overlay_hz"]))),
            })
        if cleaned_steps:
            RUNTIME_POLICY_CFG["degrade_steps"] = cleaned_steps
    TEMPO_METRICS_CFG = {
        "interval_window": max(2, int(_tempo_metrics_cfg.get("interval_window", TEMPO_METRICS_CFG["interval_window"]))),
        "baseline_window": max(2, int(_tempo_metrics_cfg.get("baseline_window", TEMPO_METRICS_CFG["baseline_window"]))),
        "stats_window": max(2, int(_tempo_metrics_cfg.get("stats_window", TEMPO_METRICS_CFG["stats_window"]))),
    }
except Exception:
    pass
if not isinstance(MODULE_POLICIES.get("legacy.event_shim"), dict):
    MODULE_POLICIES["legacy.event_shim"] = {"enabled": True, "policy": "event_driven", "interval_hz": 10.0}
_zh_pol = MODULE_POLICIES.get("plugins.zharmony")
if not isinstance(_zh_pol, dict):
    MODULE_POLICIES["plugins.zharmony"] = {"enabled": True, "policy": "event_driven", "interval_hz": 60.0}
else:
    # Chord/key detection must ingest every note event; interval throttling drops notes.
    _zh_pol["enabled"] = bool(_zh_pol.get("enabled", True))
    _zh_pol["policy"] = "event_driven"
    _zh_pol["interval_hz"] = float(_zh_pol.get("interval_hz", 60.0))
    MODULE_POLICIES["plugins.zharmony"] = _zh_pol
try:
    save_section("core", {
        "fps": float(FPS),
        "header_scroll_speed": float(HEADER_SCROLL_SPEED),
        "ipc": {
            "enabled": bool(IPC_ENABLED),
            "socket_path": str(IPC_SOCKET_PATH),
            "publish_hz": float(IPC_PUBLISH_HZ),
        },
        "module_scheduler": {
            "overload_cost_ms": float(MODULE_OVERLOAD_COST_MS),
            "modules": dict(MODULE_POLICIES),
        },
        "autoconnect": {
            "fallback_sources": list(AUTOCONNECT_FALLBACK_SOURCES),
            "fallback_destinations": list(AUTOCONNECT_FALLBACK_DESTINATIONS),
        },
        "tempo_metrics": dict(TEMPO_METRICS_CFG),
        "feature_flags": dict(CORE_FEATURE_FLAGS),
        "runtime_policy": dict(RUNTIME_POLICY_CFG),
    })
except Exception:
    pass

try:
    save_section("panic", {
        "output_name": str(PANIC_OUTPUT_NAME),
        "dst_hints": list(PANIC_DST_HINTS),
        "retry_enable": bool(PANIC_RETRY_ENABLE),
        "retry_interval": float(PANIC_RETRY_INTERVAL),
        "retry_interval_cap": float(PANIC_RETRY_INTERVAL_CAP),
    })
except Exception:
    pass

_capture_cfg = load_section("capture")
if _capture_cfg is None:
    _capture_cfg = {}
CAPTURE_BARS_TO_KEEP = int(_capture_cfg.get("bars_to_keep", 16))
CAPTURE_DUMP_BARS = int(_capture_cfg.get("dump_bars", 4))
CAPTURE_OUTPUT_DIR = str(_capture_cfg.get("output_dir", "captures"))
CAPTURE_FILE_PREFIX = str(_capture_cfg.get("file_prefix", "capture"))
CAPTURE_DEFAULT_BPM = float(_capture_cfg.get("default_bpm", 120.0))
try:
    save_section("capture", {
        "bars_to_keep": max(1, int(CAPTURE_BARS_TO_KEEP)),
        "dump_bars": max(1, int(CAPTURE_DUMP_BARS)),
        "output_dir": str(CAPTURE_OUTPUT_DIR),
        "file_prefix": str(CAPTURE_FILE_PREFIX),
        "default_bpm": max(20.0, float(CAPTURE_DEFAULT_BPM)),
    })
except Exception:
    pass

# ---------------------------------------------------------------------
# Helpers (exposed early so pages can import them safely)
# ---------------------------------------------------------------------
def draw_line(row, text):
    if _compositor is not None:
        _compositor.draw_text_line(row, text)
    else:
        sys.stdout.write(term.move_yx(row, 0) + text[:SCREEN_COLS].ljust(SCREEN_COLS))


def plugin_state_dict():
    if ENGINE:
        state = ENGINE.make_plugin_state(SCREEN_COLS, SCREEN_ROWS, y_offset=3)
        if isinstance(state, dict):
            state["render_backend"] = ACTIVE_RENDER_BACKEND
        return state
    return {
        "tick": tick_counter,
        "bar": bar_counter,
        "running": running,
        "bpm": bpm,
        "cols": SCREEN_COLS,
        "rows": SCREEN_ROWS,
        "y_offset": 3,
        "render_backend": ACTIVE_RENDER_BACKEND,
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

# De-dupe plugin list by module name (some plugins self-register on import).
_seen_plugin_names = set()
_unique_plugins = []
for _mod in PLUGINS:
    _name = getattr(_mod, "__name__", "")
    if _name in _seen_plugin_names:
        continue
    _seen_plugin_names.add(_name)
    _unique_plugins.append(_mod)
PLUGINS = _unique_plugins

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


_engine_event_diag_next = 0.0  # next time to check scheduler diagnostics

def handle_engine_event(event, msg: mido.Message):
    global _scheduler_health_status, _engine_event_diag_next
    kind = event.get("kind", "")
    if kind == "clock":
        # Clock ticks are the most frequent MIDI message (~48/sec at 120BPM).
        # Skip the expensive get_transport_state() + diagnostics parsing.
        # The UI loop refreshes transport globals each frame anyway.
        return
    snapshot = ENGINE.get_transport_state() if ENGINE else {
        "running": running,
        "tick_counter": tick_counter,
        "bar_counter": bar_counter,
        "bpm": bpm,
        "diagnostics": {},
    }
    _sync_transport_globals(snapshot)
    # Throttle scheduler health check to ~2 Hz
    now = time.monotonic()
    if now >= _engine_event_diag_next:
        _engine_event_diag_next = now + 0.5
        diag = snapshot.get("diagnostics", {}) if isinstance(snapshot.get("diagnostics"), dict) else {}
        sched = diag.get("scheduler", {}) if isinstance(diag.get("scheduler"), dict) else {}
        overloaded = sched.get("overloaded_modules", []) if isinstance(sched.get("overloaded_modules"), list) else []
        health = "sched:overload:" + ",".join(overloaded[:3]) if overloaded else "sched:ok"
        if health != _scheduler_health_status:
            _scheduler_health_status = health
            _append_runtime_log(f"[Scheduler] {health}")


# ---------------------------------------------------------------------
# Shared status slots (written by plugins, read by footer renderers)
# ---------------------------------------------------------------------
sysex_status = ""       # last sysex command summary, displayed in footer
sysex_status_time = 0.0
fps_status = ""         # rolling fps text displayed in footer
footer_status_text = "" # latest transport/status text (moved to bottom footer area)
_scheduler_health_status = ""
runtime_budget_status = ""

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


def _screensaver_module() -> ScreenSaverModule | None:
    for mod in PLUGINS:
        if hasattr(mod, "is_active") and hasattr(mod, "deactivate"):
            return mod
    return None


def _pagecycle_module() -> UserActivityModule | None:
    for mod in PLUGINS:
        if hasattr(mod, "notify_keypress"):
            return mod
    return None


def wake_screensaver() -> bool:
    ss = _screensaver_module()
    if not ss:
        return False
    was_active = bool(ss.is_active())
    ss.deactivate()
    return was_active


def set_config_section(section: str, value: dict):
    save_section(section, value)


def _on_midi_activity(msg: mido.Message) -> None:
    ss = _screensaver_module()
    if ss and ss.is_active():
        ss.deactivate()
    polydisplay.handle(msg)


SNAPSHOT_PUBLISHER = SnapshotPublisher(
    socket_path=IPC_SOCKET_PATH,
    enabled=IPC_ENABLED,
    publish_hz=IPC_PUBLISH_HZ,
)
SNAPSHOT_PUBLISHER.start()

ENGINE_MODULES = [LegacyPluginModule(mod) for mod in PLUGINS]
_pianoroll_page = PAGES.get(8)
if _pianoroll_page and hasattr(_pianoroll_page, "get_view_payload"):
    ENGINE_MODULES.append(PianoRollViewModule(_pianoroll_page.get_view_payload))

LEGACY_EVENT_SHIM_ENABLED = bool(
    (MODULE_POLICIES.get("legacy.event_shim", {}) if isinstance(MODULE_POLICIES.get("legacy.event_shim", {}), dict) else {}).get("enabled", True)
) and bool(CORE_FEATURE_FLAGS.get("contract_legacy_event_router", True))

ENGINE = MidiEngine(
    modules=ENGINE_MODULES,
    on_event=handle_engine_event,
    publisher=SNAPSHOT_PUBLISHER,
    module_policies=MODULE_POLICIES,
    overload_cost_ms=MODULE_OVERLOAD_COST_MS,
    command_hooks={
        "set_page": switch_page,
        "wake_screensaver": wake_screensaver,
        "set_config": set_config_section,
    },
    legacy_pages_provider=lambda: dict(PAGES),
    current_page_provider=lambda: int(current_page),
    plugin_state_provider=plugin_state_dict,
    midi_activity_handler=_on_midi_activity,
    legacy_event_shim_enabled=LEGACY_EVENT_SHIM_ENABLED,
    tempo_metrics=TEMPO_METRICS_CFG,
)
ENGINE.configure_capture({
    "bars_to_keep": CAPTURE_BARS_TO_KEEP,
    "dump_bars": CAPTURE_DUMP_BARS,
    "output_dir": CAPTURE_OUTPUT_DIR,
    "file_prefix": CAPTURE_FILE_PREFIX,
    "default_bpm": CAPTURE_DEFAULT_BPM,
})
SNAPSHOT_PUBLISHER.set_command_handler(ENGINE.handle_command)

def ui_loop():
    global last_page, current_page, exit_flag, last_header, SCREEN_COLS, SCREEN_ROWS
    global _header_scroll_offset, _header_scroll_last_time
    global _auto_scroll_offset, _auto_scroll_last_time, _auto_last_msg, _auto_last_window

    if _compositor is not None:
        # Compositor mode: use fixed fb0 dimensions, no terminal context.
        # Redirect stdout to /dev/null so that legacy page draw() calls and
        # blessed escape sequences don't reach fbcon, which would fight us
        # for fb0 ownership.
        SCREEN_COLS = _compositor.comp.cols
        SCREEN_ROWS = _compositor.comp.rows
        _orig_stdout = sys.stdout
        sys.stdout = open(os.devnull, "w")
        try:
            _ui_loop_body()
        finally:
            sys.stdout.close()
            sys.stdout = _orig_stdout
        return

    with term.fullscreen(), term.hidden_cursor():
        sys.stdout.write(term.home + term.clear)
        _ui_loop_body()


def _ui_loop_body():
    global last_page, current_page, exit_flag, last_header, SCREEN_COLS, SCREEN_ROWS
    global _header_scroll_offset, _header_scroll_last_time
    global _auto_scroll_offset, _auto_scroll_last_time, _auto_last_msg, _auto_last_window
    global runtime_budget_status
    _frame_budget = 1.0 / FPS
    _runtime_policy = RuntimeBudgetPolicy(FPS, RUNTIME_POLICY_CFG)
    _base_degrade = {
        "page_cache_ttl": max(0.05, float(RUNTIME_POLICY_CFG.get("page_cache_ttl_ms", 200)) / 1000.0),
        "notes_page_cache_interval": 1.0 / max(2.0, float(RUNTIME_POLICY_CFG.get("notes_page_cache_hz", 30.0))),
        "notes_badge_interval": 1.0 / max(2.0, float(RUNTIME_POLICY_CFG.get("notes_badge_hz", 24.0))),
        "plugin_overlay_interval": 1.0 / max(2.0, float(RUNTIME_POLICY_CFG.get("plugin_overlay_hz", 10.0))),
    }
    _runtime_steps = []
    for _step in RUNTIME_POLICY_CFG.get("degrade_steps", []):
        if isinstance(_step, dict):
            _runtime_steps.append({
                "page_cache_ttl": max(0.05, float(_step.get("page_cache_ttl_ms", 200)) / 1000.0),
                "notes_page_cache_interval": 1.0 / max(2.0, float(_step.get("notes_page_cache_hz", 30.0))),
                "notes_badge_interval": 1.0 / max(2.0, float(_step.get("notes_badge_hz", 24.0))),
                "plugin_overlay_interval": 1.0 / max(2.0, float(_step.get("plugin_overlay_hz", 10.0))),
            })
    if not _runtime_steps:
        _runtime_steps = [dict(_base_degrade)]
    _runtime_tuning = dict(_base_degrade)

    def _apply_runtime_level(level: int):
        idx = max(0, min(level, len(_runtime_steps)))
        if idx <= 0:
            return dict(_base_degrade)
        return dict(_runtime_steps[idx - 1])
    _do_plugin_draw = not os.environ.get("MIDICRT_DISABLE_PLUGIN_DRAW")
    # --- Profiling instrumentation ---
    _prof_enabled = bool(os.environ.get("MIDICRT_PROFILE"))
    _prof_n = 0
    _prof_accum = {}
    _prof_last_dump = time.monotonic()
    _PROF_INTERVAL = 5.0  # dump every 5s
    def _pt(label, t0):  # record elapsed since t0, return now
        if _prof_enabled:
            _prof_accum[label] = _prof_accum.get(label, 0.0) + (time.monotonic() - t0)
        return time.monotonic()
    # Cache plugin draw signatures once (avoid per-frame inspect overhead)
    _plugin_draw_takes_state = {}
    for _mod in PLUGINS:
        if hasattr(_mod, "draw"):
            try:
                _plugin_draw_takes_state[id(_mod)] = len(signature(_mod.draw).parameters) == 1
            except Exception:
                _plugin_draw_takes_state[id(_mod)] = False
    while not exit_flag:
      try:
        _frame_t0 = time.monotonic()
        _runtime_tuning = _apply_runtime_level(_runtime_policy.level)
        _pt0 = _frame_t0
        # refresh screen size each frame so pages/plugins can use all space
        try:
            if _compositor is None:
                w = getattr(term, 'width', SCREEN_COLS) or SCREEN_COLS
                h = getattr(term, 'height', SCREEN_ROWS) or SCREEN_ROWS
                if w != SCREEN_COLS or h != SCREEN_ROWS:
                    SCREEN_COLS = w
                    SCREEN_ROWS = h
                    last_header = ""
        except Exception:
            pass

        try:
            ENGINE.set_ui_context(cols=SCREEN_COLS, rows=SCREEN_ROWS, y_offset=3, current_page=current_page)
        except Exception:
            pass

        if _compositor is not None:
            _compositor.frame_clear()
        elif current_page != last_page:
            sys.stdout.write(term.home + term.clear)

        # Handle page switch: reset scroll state
        if current_page != last_page:
            last_page = current_page
            last_header = ""  # force header redraw after clear

        snapshot = ENGINE.get_transport_state() if ENGINE else {
            "tick_counter": tick_counter,
            "bar_counter": bar_counter,
            "running": running,
            "bpm": bpm,
        }
        # get_snapshot() is expensive (iterates all modules, builds full state dict).
        # Cache it at 10 Hz — module outputs/diagnostics don't need per-frame freshness.
        schema_snapshot = {}
        if ENGINE:
            _snap_now = time.monotonic()
            if _snap_now >= getattr(ui_loop, "_schema_snap_next_t", 0.0):
                try:
                    ui_loop._schema_snap_cache = (ENGINE.get_snapshot() or {}).get("schema", {})
                except Exception:
                    ui_loop._schema_snap_cache = {}
                ui_loop._schema_snap_next_t = _snap_now + 0.1  # 10 Hz
            schema_snapshot = getattr(ui_loop, "_schema_snap_cache", {})
        transport_schema = schema_snapshot.get("transport") if isinstance(schema_snapshot.get("transport"), dict) else {}
        ui_snapshot = {
            "transport": {
                "running": bool(transport_schema.get("running", snapshot.get("running", False))),
                "bpm": float(transport_schema.get("bpm", snapshot.get("bpm", 0.0))),
                "bar": int(transport_schema.get("bar", snapshot.get("bar_counter", 0))),
                "tick": int(transport_schema.get("tick", snapshot.get("tick_counter", 0))),
                "time_signature": str(transport_schema.get("meter_estimate", "") or ""),
            },
            "status_text": str(schema_snapshot.get("status_text", "") or ""),
        }
        state = plugin_state_dict()
        _pt0 = _pt("snapshot", _pt0)

        # --- COMPOSITOR WHOLE-FRAME SKIP ---
        # When page content + transport state are unchanged, skip all rendering.
        # fb0 retains the last flushed frame without re-writing.
        page = PAGES.get(current_page)
        _comp_skip_frame = False
        if _compositor is not None and page is not None and current_page != 1:
            try:
                _ck_fn_skip = getattr(page, "compositor_cache_key", None)
                _skip_key = _ck_fn_skip() if callable(_ck_fn_skip) else None
                if _skip_key is not None:
                    _full_frame_key_now = (
                        _skip_key,
                        bool(snapshot.get("running", False)),
                        int(snapshot.get("bar_counter", 0)),
                        int(snapshot.get("bpm", 0.0)),
                        int(snapshot.get("tick_counter", 0)) // 24,
                        bool(sysex_status and (time.time() - sysex_status_time) < 3.0),
                    )
                    _full_age = time.monotonic() - getattr(ui_loop, "_full_frame_t", 0.0)
                    if (_full_frame_key_now == getattr(ui_loop, "_full_frame_key", None)
                            and _full_age < 0.05):
                        _comp_skip_frame = True
            except Exception:
                pass

        if _comp_skip_frame:
            if page and hasattr(page, "on_tick"):
                try:
                    page.on_tick(state)
                except Exception:
                    pass
            if _prof_enabled:
                _pt("total", _frame_t0)
                _prof_n += 1
                _now_prof = time.monotonic()
                if _now_prof - _prof_last_dump >= _PROF_INTERVAL:
                    _prof_last_dump = _now_prof
                    lines = [f"frames={_prof_n}  page={current_page}"]
                    for k, v in sorted(_prof_accum.items(), key=lambda x: -x[1]):
                        lines.append(f"  {k:<14s}: {v/_prof_n*1000:.2f}ms")
                    try:
                        with open("/tmp/midicrt_perf.txt", "w") as _pf:
                            _pf.write("\n".join(lines) + "\n")
                    except Exception:
                        pass
                    _prof_accum.clear()
                    _prof_n = 0
            time.sleep(max(0, _frame_budget - (time.monotonic() - _frame_t0)))
            continue

        # --- HEADER (row 0) — scrolling marquee when wider than screen
        _now = time.time()
        # page_titles only changes when PAGES changes (rare) — cache join + doubled string.
        if not hasattr(ui_loop, "_header_title_str"):
            ui_loop._header_title_str = ""
            ui_loop._header_doubled = ""
        page_titles = ui_loop._header_title_str
        if page_titles != last_header or not page_titles:
            page_titles = "  ".join(
                f"[{pid}:{p.PAGE_NAME}]" for pid, p in sorted(PAGES.items())
            )
            ui_loop._header_title_str = page_titles
            ui_loop._header_doubled = (page_titles + "    ") * 2
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
            offset = int(_header_scroll_offset) % (len(page_titles) + 4)
            draw_line(0, ui_loop._header_doubled[offset:offset + SCREEN_COLS])

        # --- TRANSPORT (row 1)
        transport_widget = build_transport_widget(ui_snapshot)
        status = "RUN" if transport_widget.running else "STOP"
        metronome = "●" if transport_widget.running and (transport_widget.tick % 24) < 3 else "○"
        base = f" {status:<4}  {transport_widget.bpm:6.1f} BPM   BAR {transport_widget.bar:04d}   {metronome}"
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

        _pt0 = _pt("header", _pt0)
        # --- STATUS (row 2): schema-backed footer payload
        _frame_now = time.monotonic()
        _frame_dt = _frame_now - getattr(ui_loop, "_frame_last_t", _frame_now)
        ui_loop._frame_last_t = _frame_now
        global fps_status, footer_status_text
        fps_status = f"fps:{1.0/_frame_dt:.1f}" if _frame_dt > 0 else "fps:--"
        footer_status_text = str(ui_snapshot.get("status_text", "") or "")
        footer_right_parts = [p for p in (fps_status, _scheduler_health_status, runtime_budget_status) if p]
        if sysex_status and (time.time() - sysex_status_time) < 3.0:
            footer_right_parts.append(sysex_status)
        ui_snapshot["fps_status"] = fps_status
        ui_snapshot["footer"] = {
            "left": footer_status_text,
            "right": " | ".join(footer_right_parts),
        }
        # Keep row 2 clear; footer/status now lives in the bottom loopprogress meter.
        draw_line(2, "")

        _pt0 = _pt("footer", _pt0)
        # --- SCREENSAVER CHECK: skip all drawing if active
        # In compositor mode the screensaver writes zeros directly to fb0 via
        # its own mmap, fighting the compositor.  Skip it entirely here; the
        # compositor's own frame_clear()/flush() cycle handles blanking.
        _ss = _screensaver_module()
        if _compositor is None and _ss and _ss.is_active():
            _ss.draw(state)
            sys.stdout.flush()
            time.sleep(max(0, _frame_budget - (time.monotonic() - _frame_t0)))
            continue

        # --- PAGE CONTENT (start row 3)
        if page and hasattr(page, "on_tick"):
            try:
                page.on_tick(state)
            except Exception:
                pass
        _content_cache_hit = False
        _used_notes_page_cache = False
        if _compositor is not None and current_page == 1:
            cache = getattr(ui_loop, "_notes_page_cache", None)
            cache_next = getattr(ui_loop, "_notes_page_next_t", 0.0)
            if cache is not None and time.monotonic() < cache_next:
                try:
                    y0_px = 3 * _compositor.comp.char_h
                    h_px = cache.shape[0]
                    w_px = cache.shape[1]
                    if w_px == _compositor.comp._buf.shape[1]:
                        _compositor.comp._buf[y0_px:y0_px + h_px, :w_px] = cache
                        _content_cache_hit = True
                        _used_notes_page_cache = True
                except Exception:
                    _content_cache_hit = False
                    _used_notes_page_cache = False

        # Generic compositor page-content cache: pages can expose compositor_cache_key()
        # to skip re-rendering when their content hasn't changed.
        # A max TTL of 200ms ensures the display refreshes at least 5 Hz even for static content.
        _PAGE_CACHE_MAX_AGE = _runtime_tuning["page_cache_ttl"]
        if not _content_cache_hit and _compositor is not None and page and current_page != 1:
            try:
                cache_key_fn = getattr(page, "compositor_cache_key", None)
                if callable(cache_key_fn):
                    new_key = cache_key_fn()
                    cache_age = time.monotonic() - getattr(ui_loop, "_page_cache_t", 0.0)
                    if (new_key is not None
                            and new_key == getattr(ui_loop, "_page_cache_key", None)
                            and cache_age < _PAGE_CACHE_MAX_AGE):
                        cached_buf = getattr(ui_loop, "_page_cache_buf", None)
                        if cached_buf is not None:
                            y0_px = 3 * _compositor.comp.char_h
                            h_px = cached_buf.shape[0]
                            w_px = cached_buf.shape[1]
                            if w_px == _compositor.comp._buf.shape[1]:
                                _compositor.comp._buf[y0_px:y0_px + h_px, :w_px] = cached_buf
                                _content_cache_hit = True
            except Exception:
                pass

        if not _content_cache_hit and page:
            try:
                content_rows = max(0, SCREEN_ROWS - 3)
                if hasattr(page, "build_widget"):
                    widget = page.build_widget(state)
                elif hasattr(page, "draw"):
                    if CORE_FEATURE_FLAGS.get("contract_page_views", False):
                        payload = capture_legacy_page_view(page.draw, state, draw_line).to_dict()
                        widget = widget_from_page_view(payload)
                    else:
                        widget = _widget_from_legacy_draw(page.draw, state, draw_line)
                else:
                    widget = None
                if widget is None:
                    draw_line(3, f"No page loaded for {current_page}")
                elif _compositor is not None:
                    _compositor.render(widget, Frame(cols=SCREEN_COLS, rows=content_rows))
                else:
                    rendered = text_renderer.render(widget, Frame(cols=SCREEN_COLS, rows=content_rows))
                    for idx, line in enumerate(rendered):
                        draw_line(3 + idx, line)
            except Exception as e:
                draw_line(3, f"[Error {current_page}] {e}")
        elif not _content_cache_hit:
            draw_line(3, f"No page loaded for {current_page}")

        _pt0 = _pt("page", _pt0)

        # Save generic compositor page-content cache after a fresh render.
        if not _content_cache_hit and _compositor is not None and page and current_page != 1:
            try:
                cache_key_fn = getattr(page, "compositor_cache_key", None)
                if callable(cache_key_fn):
                    new_key = cache_key_fn()
                    if new_key is not None:
                        y0_px = 3 * _compositor.comp.char_h
                        h_px = max(0, SCREEN_ROWS - 3) * _compositor.comp.char_h
                        y1_px = min(_compositor.comp._buf.shape[0], y0_px + h_px)
                        ui_loop._page_cache_buf = _compositor.comp._buf[y0_px:y1_px, :].copy()
                        ui_loop._page_cache_key = new_key
                        ui_loop._page_cache_t = time.monotonic()
            except Exception:
                pass

        # --- PLUGIN VISUALS (respect y_offset)
        if _do_plugin_draw and not _used_notes_page_cache:
            now_plugins = time.monotonic()
            if (
                now_plugins >= getattr(ui_loop, "_plugin_cache_next_t", 0.0)
                or not hasattr(ui_loop, "_plugin_overlay_cache")
            ):
                ui_loop._plugin_overlay_cache = capture_plugin_overlay_widget(
                    PLUGINS,
                    state,
                    SCREEN_COLS,
                    SCREEN_ROWS,
                    _plugin_draw_takes_state,
                )
                ui_loop._plugin_cache_next_t = now_plugins + _runtime_tuning["plugin_overlay_interval"]
            overlay_rows = compose_overlay_rows(
                getattr(ui_loop, "_plugin_overlay_cache"),
                cols=SCREEN_COLS,
                rows=SCREEN_ROWS,
                start_row=state.get("y_offset", 0),
            )
            if _compositor is not None:
                for row_idx, row_text in overlay_rows:
                    _compositor.draw_text_line(row_idx, row_text)
            else:
                for row_idx, row_text in overlay_rows:
                    draw_line(row_idx, row_text)

        _pt0 = _pt("plugins", _pt0)
        if _compositor is not None and current_page == 1 and not _used_notes_page_cache:
            try:
                y0_px = 3 * _compositor.comp.char_h
                h_px = max(0, SCREEN_ROWS - 3) * _compositor.comp.char_h
                y1_px = min(_compositor.comp._buf.shape[0], y0_px + h_px)
                ui_loop._notes_page_cache = _compositor.comp._buf[y0_px:y1_px, :].copy()
                ui_loop._notes_page_next_t = time.monotonic() + _runtime_tuning["notes_page_cache_interval"]
            except Exception:
                pass

        _pt0 = _pt("page_cache", _pt0)
        if _compositor is not None:
            if current_page == 1:
                badge_now = time.monotonic()
                badge_next = getattr(ui_loop, "_notes_badge_data_next_t", 0.0)
                if badge_now >= badge_next or not hasattr(ui_loop, "_notes_badge_levels"):
                    badge_levels = None
                    badge_pcs = set()
                    badge_roll = None
                    spectrum_page = PAGES.get(9)
                    if spectrum_page:
                        try:
                            if not getattr(ui_loop, "_notes_badge_spec_ready", False):
                                if hasattr(spectrum_page, "register_spectrum_tap"):
                                    spectrum_page.register_spectrum_tap(1)
                                if hasattr(spectrum_page, "ensure_background"):
                                    spectrum_page.ensure_background()
                                ui_loop._notes_badge_spec_ready = True
                            if hasattr(spectrum_page, "get_levels"):
                                badge_levels = spectrum_page.get_levels()
                        except Exception:
                            badge_levels = None
                    try:
                        pr_page = PAGES.get(8)
                        if pr_page and hasattr(pr_page, "get_view_payload"):
                            cand = pr_page.get_view_payload()
                            if isinstance(cand, dict):
                                badge_roll = cand
                    except Exception:
                        badge_roll = None
                    try:
                        active_by_ch = ENGINE.get_active_notes() if ENGINE else {}
                        if isinstance(active_by_ch, dict):
                            for notes in active_by_ch.values():
                                if isinstance(notes, (list, tuple, set)):
                                    for note in notes:
                                        badge_pcs.add(int(note) % 12)
                    except Exception:
                        badge_pcs = set()
                    ui_loop._notes_badge_levels = badge_levels
                    ui_loop._notes_badge_pcs = badge_pcs
                    ui_loop._notes_badge_roll = badge_roll
                    ui_loop._notes_badge_data_next_t = badge_now + _runtime_tuning["notes_badge_interval"]
                _compositor.draw_notes_badge(
                    spectrum_levels=getattr(ui_loop, "_notes_badge_levels", None),
                    active_pcs=getattr(ui_loop, "_notes_badge_pcs", set()),
                    roll_payload=getattr(ui_loop, "_notes_badge_roll", None),
                )
            _pt0 = _pt("badge", _pt0)
            _do_flush = True
            # Optional per-page flush hint: skip redundant framebuffer flushes
            # when content is unchanged, but enforce a max interval safety net.
            if page is not None:
                try:
                    _fh_fn = getattr(page, "compositor_flush_hint", None)
                    if callable(_fh_fn):
                        _hint = _fh_fn()
                        _hint_dirty = True
                        _hint_max_interval = 0.0
                        if isinstance(_hint, dict):
                            _hint_dirty = bool(_hint.get("dirty", True))
                            _hint_max_interval = float(_hint.get("max_interval", 0.0))
                        elif isinstance(_hint, (tuple, list)):
                            if len(_hint) >= 1:
                                _hint_dirty = bool(_hint[0])
                            if len(_hint) >= 2:
                                _hint_max_interval = float(_hint[1])
                        elif isinstance(_hint, bool):
                            _hint_dirty = bool(_hint)
                        _now_flush = time.monotonic()
                        _last_flush = float(getattr(ui_loop, "_compositor_last_flush_t", 0.0))
                        if (not _hint_dirty) and _hint_max_interval > 0.0 and (_now_flush - _last_flush) < _hint_max_interval:
                            _do_flush = False
                except Exception:
                    _do_flush = True
            if _do_flush:
                _compositor.frame_flush()
                ui_loop._compositor_last_flush_t = time.monotonic()
                _pt0 = _pt("flush", _pt0)
            # Save whole-frame compositor cache key so subsequent frames can be skipped.
            if page is not None and current_page != 1:
                try:
                    _ck_fn_ff = getattr(page, "compositor_cache_key", None)
                    if callable(_ck_fn_ff):
                        _fk_ff = _ck_fn_ff()
                        if _fk_ff is not None:
                            ui_loop._full_frame_key = (
                                _fk_ff,
                                bool(snapshot.get("running", False)),
                                int(snapshot.get("bar_counter", 0)),
                                int(snapshot.get("bpm", 0.0)),
                                int(snapshot.get("tick_counter", 0)) // 24,
                                bool(sysex_status and (time.time() - sysex_status_time) < 3.0),
                            )
                            ui_loop._full_frame_t = time.monotonic()
                except Exception:
                    pass
        else:
            sys.stdout.flush()
        if _prof_enabled:
            _pt("total", _frame_t0)
            _prof_n += 1
            _now_prof = time.monotonic()
            if _now_prof - _prof_last_dump >= _PROF_INTERVAL:
                _prof_last_dump = _now_prof
                lines = [f"frames={_prof_n}  page={current_page}"]
                for k, v in sorted(_prof_accum.items(), key=lambda x: -x[1]):
                    lines.append(f"  {k:<14s}: {v/_prof_n*1000:.2f}ms")
                try:
                    with open("/tmp/midicrt_perf.txt", "w") as _pf:
                        _pf.write("\n".join(lines) + "\n")
                except Exception:
                    pass
                _prof_accum.clear()
                _prof_n = 0
        _frame_dt_done = time.monotonic() - _frame_t0
        _runtime_policy.note_frame(_frame_dt_done)
        _step = _runtime_policy.maybe_step()
        if _step:
            _append_runtime_log(
                f"[RuntimePolicy] step={_step} level={_runtime_policy.level} "
                f"avg_ms={_runtime_policy.avg_frame_ms():.2f} over_budget={_runtime_policy.over_budget_ratio_value():.2f}"
            )
        runtime_budget_status = (
            f"budget:{_runtime_policy.avg_frame_ms():.1f}ms "
            f"ovr:{_runtime_policy.over_budget_ratio_value()*100.0:.0f}% "
            f"lvl:{_runtime_policy.level}"
        )
        time.sleep(max(0, _frame_budget - (time.monotonic() - _frame_t0)))
      except Exception:
        import traceback as _tb
        _exc_txt = _tb.format_exc()
        _append_runtime_log("ui_loop exception:\n" + _exc_txt)
        try:
            with open("/tmp/midicrt_exc.txt", "a") as _ef:
                _ef.write(_exc_txt)
        except Exception:
            pass
        time.sleep(max(0, _frame_budget - (time.monotonic() - _frame_t0)))

# ---------------------------------------------------------------------
# Autoconnect + keyboard listener + main
# ---------------------------------------------------------------------
_client_re = re.compile(r"^client\s+(\d+):\s+'([^']+)'")
_port_re = re.compile(r"^\s+(\d+)\s+'([^']+)'")
_connect_to_re = re.compile(r"^\s+Connecting To:\s*(.+)$")
_connect_from_re = re.compile(r"^\s+Connected From:\s*(.+)$")
_endpoint_re = re.compile(r"(\d+):(\d+)")
_forced_pair_re = re.compile(r"^\s*(\d+:\d+)\s*->\s*(\d+:\d+)\s*$")


def _log_autoconnect(msg: str):
    AUTOCONNECT_LOG.append(msg)
    if len(AUTOCONNECT_LOG) > 32:
        del AUTOCONNECT_LOG[0]


def _install_midi_error_filter(port) -> None:
    """Suppress noisy transient RtMidi ALSA warnings (EAGAIN)."""
    try:
        rt = getattr(port, "_rt", None)
        if rt is None or not hasattr(rt, "set_error_callback"):
            return

        def _on_midi_error(_etype, msg, _data=None):
            text = str(msg) if msg is not None else ""
            if "Resource temporarily unavailable" in text:
                return
            if text:
                _log_autoconnect(f"[MIDI] {text[:80]}")

        # Keep callback alive for the lifetime of the port.
        setattr(port, "_midicrt_error_cb", _on_midi_error)
        rt.set_error_callback(_on_midi_error)
    except Exception:
        pass


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

    return parse_aconnect_output(result.stdout)


def _parse_aconnect_edges():
    """Return a set of existing sequencer connections as (src_id, dst_id)."""
    try:
        result = subprocess.run(
            ["aconnect", "-l"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        _log_autoconnect(f"[AutoConnect] Unable to run aconnect -l: {exc}")
        return set()

    edges = set()
    current_client_id = None
    current_port = None
    for line in result.stdout.splitlines():
        m_client = _client_re.match(line)
        if m_client:
            current_client_id = m_client.group(1)
            current_port = None
            continue
        m_port = _port_re.match(line)
        if m_port:
            current_port = f"{current_client_id}:{m_port.group(1)}" if current_client_id else None
            continue
        if not current_port:
            continue

        m_to = _connect_to_re.match(line)
        if m_to:
            for dst_client, dst_port in _endpoint_re.findall(m_to.group(1)):
                edges.add((current_port, f"{dst_client}:{dst_port}"))
            continue
        m_from = _connect_from_re.match(line)
        if m_from:
            for src_client, src_port in _endpoint_re.findall(m_from.group(1)):
                edges.add((f"{src_client}:{src_port}", current_port))
    return edges


def _parse_forced_pairs(forced_env: str):
    valid_pairs = []
    rejected = []
    for part in forced_env.split(","):
        token = part.strip()
        if not token:
            continue
        m = _forced_pair_re.match(token)
        if not m:
            rejected.append(token)
            continue
        valid_pairs.append((m.group(1), m.group(2)))
    return valid_pairs, rejected


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
    existing_edges = _parse_aconnect_edges()

    def collect_candidates(entries, hints, fallbacks):
        by_id = {}

        def add_candidate(port_id, client_name, port_name, confidence, reason):
            prev = by_id.get(port_id)
            candidate = {
                "id": port_id,
                "client": client_name,
                "port": port_name,
                "confidence": confidence,
                "reason": reason,
            }
            if prev is None or confidence > prev["confidence"]:
                by_id[port_id] = candidate

        for client_id, client_name, port_id, port_name in entries:
            port_full = f"{client_id}:{port_id}"
            client_lower = client_name.lower()
            port_lower = port_name.lower()
            for hint in hints:
                hint_lower = hint.lower()
                if hint_lower == port_lower:
                    add_candidate(port_full, client_name, port_name, 3, f"exact port-name match '{hint}'")
                    break
                if hint_lower in client_lower:
                    add_candidate(port_full, client_name, port_name, 2, f"client-name match '{hint}'")
                    break
        for fallback in fallbacks:
            add_candidate(fallback, "Fallback", "Configured fallback", 1, "configured fallback")

        return sorted(by_id.values(), key=lambda c: (-c["confidence"], c["id"]))

    src_candidates = collect_candidates(outputs, src_hints, AUTOCONNECT_FALLBACK_SOURCES)
    dst_candidates = collect_candidates(inputs, dst_hints, AUTOCONNECT_FALLBACK_DESTINATIONS)

    forced_env = os.environ.get("MIDICRT_AUTOCONNECT_FORCE", "")
    forced_pairs, rejected_pairs = _parse_forced_pairs(forced_env) if forced_env else ([], [])
    for bad in rejected_pairs:
        _log_autoconnect(f"[AutoConnect] Rejected malformed forced pair '{bad}' (expected N:M->N:M)")

    if not [c for c in src_candidates if c["confidence"] > 1]:
        _log_autoconnect(f"[AutoConnect] Could not locate Cirklon output; hints: {src_hints}")
    if not [c for c in dst_candidates if c["confidence"] > 1]:
        _log_autoconnect(f"[AutoConnect] Could not locate monitor input; hints: {dst_hints}")

    if not src_candidates or not dst_candidates:
        if outputs:
            _log_autoconnect("[AutoConnect] Available outputs:")
            for client_id, client_name, port_id, port_name in outputs:
                _log_autoconnect(f"   {client_id}:{port_id}  {client_name} — {port_name}")
        if inputs:
            _log_autoconnect("[AutoConnect] Available inputs:")
            for client_id, client_name, port_id, port_name in inputs:
                _log_autoconnect(f"   {client_id}:{port_id}  {client_name} — {port_name}")

    summary = {
        "forced_pairs": [{"src": src_id, "dst": dst_id} for src_id, dst_id in forced_pairs],
        "rejected_forced": rejected_pairs,
        "src_candidates": src_candidates,
        "dst_candidates": dst_candidates,
        "attempted": [],
        "winning_pair": None,
        "exhausted_reason": None,
    }

    attempted = set()

    # If explicit forced pairs were supplied, try them first.
    for src_id, dst_id in forced_pairs:
        if (src_id, dst_id) in existing_edges:
            summary["winning_pair"] = {
                "src": src_id,
                "dst": dst_id,
                "reason": "already connected (forced pair)",
            }
            summary["attempted"].append({"src": src_id, "dst": dst_id, "status": "already_connected", "reason": "forced pair"})
            _log_autoconnect(f"[AutoConnect] Already connected {src_id} → {dst_id}; skipping connect call")
            _log_autoconnect(f"[AutoConnect] Summary {json.dumps(summary, sort_keys=True)}")
            return
        summary["attempted"].append({"src": src_id, "dst": dst_id, "status": "attempted", "reason": "forced pair"})
        if _connect_pair(src_id, dst_id, existing_edges):
            summary["winning_pair"] = {"src": src_id, "dst": dst_id, "reason": "forced pair connected"}
            _log_autoconnect(f"[AutoConnect] Summary {json.dumps(summary, sort_keys=True)}")
            return
        attempted.add((src_id, dst_id))

    for src in src_candidates:
        for dst in dst_candidates:
            src_id = src["id"]
            dst_id = dst["id"]
            key = (src_id, dst_id)
            if key in attempted:
                continue
            attempted.add(key)
            _log_autoconnect(
                f"[AutoConnect] Trying {src['client']}:{src['port']} ({src_id}) → {dst['client']}:{dst['port']} ({dst_id})"
            )
            if key in existing_edges:
                summary["attempted"].append({"src": src_id, "dst": dst_id, "status": "already_connected", "reason": f"{src['reason']} + {dst['reason']}"})
                summary["winning_pair"] = {"src": src_id, "dst": dst_id, "reason": "already connected"}
                _log_autoconnect(f"[AutoConnect] Already connected {src_id} → {dst_id}; skipping connect call")
                _log_autoconnect(f"[AutoConnect] Summary {json.dumps(summary, sort_keys=True)}")
                return
            summary["attempted"].append({"src": src_id, "dst": dst_id, "status": "attempted", "reason": f"{src['reason']} + {dst['reason']}"})
            if _connect_pair(src_id, dst_id, existing_edges):
                summary["winning_pair"] = {"src": src_id, "dst": dst_id, "reason": f"{src['reason']} + {dst['reason']}"}
                _log_autoconnect(f"[AutoConnect] Summary {json.dumps(summary, sort_keys=True)}")
                return

    summary["exhausted_reason"] = "no candidate pair connected"
    _log_autoconnect(f"[AutoConnect] Summary {json.dumps(summary, sort_keys=True)}")
    _log_autoconnect("[AutoConnect] Exhausted attempts; please connect manually.")


def autoconnect_panic_output():
    """Attempt to connect panic output port to USB MIDI out."""
    global PANIC_AUTOCONNECT_DONE
    if PANIC_AUTOCONNECT_DONE or not PANIC_OUT_VIRTUAL:
        return "skipped"

    src_hints = [PANIC_OUTPUT_NAME]
    dst_hints = PANIC_DST_HINTS

    outs = _parse_aconnect("-o")
    ins = _parse_aconnect("-i")
    existing_edges = _parse_aconnect_edges()

    def best_match(entries, hints):
        for hint in hints:
            hint_lower = hint.lower()
            for client_id, client_name, port_id, port_name in entries:
                if hint_lower == port_name.lower():
                    return f"{client_id}:{port_id}", client_name, port_name, "exact port-name match"
        for hint in hints:
            hint_lower = hint.lower()
            for client_id, client_name, port_id, port_name in entries:
                if hint_lower in client_name.lower():
                    return f"{client_id}:{port_id}", client_name, port_name, "client-name match"
        return None, None, None, None

    src_id, src_client, src_port, src_reason = best_match(outs, src_hints)
    dst_id, dst_client, dst_port, dst_reason = best_match(ins, dst_hints)

    if not src_id or not dst_id:
        return "missing_ports"

    if _connection_exists(src_id, dst_id):
        PANIC_AUTOCONNECT_DONE = True
        return "already_connected"

    if (src_id, dst_id) in existing_edges:
        _log_autoconnect(f"[Panic] Already connected {src_id} → {dst_id}; src={src_reason}, dst={dst_reason}")
        PANIC_AUTOCONNECT_DONE = True
        return "already_connected"

    if _connect_pair(src_id, dst_id, existing_edges):
        _log_autoconnect(f"[Panic] Connected {src_client}:{src_port} ({src_id}) → {dst_client}:{dst_port} ({dst_id}); src={src_reason}, dst={dst_reason}")
        PANIC_AUTOCONNECT_DONE = True
        return "connected"
    else:
        _log_autoconnect(f"[Panic] Connect failed for {src_id} → {dst_id}")
        return "connect_failed"


def _panic_autoconnect_retry_loop():
    """Retry panic output autoconnect until connected or shutdown."""
    if not PANIC_RETRY_ENABLE:
        return
    interval = max(0.25, float(PANIC_RETRY_INTERVAL))
    interval_cap = max(interval, float(PANIC_RETRY_INTERVAL_CAP))
    while not globals().get("exit_flag", False):
        if PANIC_AUTOCONNECT_DONE or not PANIC_OUT_VIRTUAL:
            return
        status = autoconnect_panic_output()
        if status in {"connected", "already_connected", "skipped"}:
            return
        time.sleep(interval)
        interval = min(interval_cap, interval * 1.5)


def _connect_pair(src_id: str, dst_id: str, existing_edges=None) -> bool:
    if existing_edges is None:
        existing_edges = _parse_aconnect_edges()
    if (src_id, dst_id) in existing_edges:
        _log_autoconnect(f"[AutoConnect] Already connected {src_id} → {dst_id}; skipping.")
        return True
    for attempt in range(5):
        try:
            subprocess.run(
                ["aconnect", src_id, dst_id],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            _log_autoconnect(f"[AutoConnect] Connected {src_id} → {dst_id}.")
            existing_edges.add((src_id, dst_id))
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




def _close_port_safely(port, timeout_s: float = 0.5) -> None:
    """Best-effort, non-blocking MIDI port close.

    RtMidi close can occasionally hang during shutdown on this target.
    Close in a daemon thread and continue if timeout expires.
    """
    if port is None:
        return

    done = threading.Event()

    def _do_close():
        try:
            port.close()
        except Exception:
            pass
        finally:
            done.set()

    t = threading.Thread(target=_do_close, daemon=True, name="midi-close")
    t.start()
    t.join(max(0.0, float(timeout_s)))
    if not done.is_set():
        _append_runtime_log("[MIDI] port close timed out; continuing asynchronously")


def _open_preferred_input():
    """Open preferred MIDI input with hardware-first strategy + virtual fallback."""
    direct_input_name = _pick_midi_input_name()
    if direct_input_name:
        port = mido.open_input(direct_input_name)
        _install_midi_error_filter(port)
        status = f"[MIDI] open success attempt=1 mode=direct port={direct_input_name}"
        _log_autoconnect(status)
        _append_runtime_log(status)
        return port, direct_input_name, "direct"

    port = mido.open_input("GreenCRT Monitor", virtual=True)
    _install_midi_error_filter(port)
    try:
        target_name = getattr(port, "name", "GreenCRT Monitor")
    except Exception:
        target_name = "GreenCRT Monitor"
    autoconnect_dynamic(target_name)
    status = f"[MIDI] open success attempt=1 mode=virtual port={target_name}"
    _log_autoconnect(status)
    _append_runtime_log(status)
    return port, target_name, "virtual"


def _reopen_input_port(prev_port, attempt, reason):
    _close_port_safely(prev_port)
    backoff = min(5.0, 0.25 * (2 ** max(0, attempt - 1)))
    fail_status = f"[MIDI] reopen attempt={attempt} waiting={backoff:.2f}s reason={reason}"
    _log_autoconnect(fail_status)
    _append_runtime_log(fail_status)
    if not exit_flag:
        time.sleep(backoff)
    port, selected_name, mode = _open_preferred_input()
    ok_status = f"[MIDI] reopen success attempt={attempt} mode={mode} port={selected_name}"
    _log_autoconnect(ok_status)
    _append_runtime_log(ok_status)
    return port


def _pick_midi_input_name() -> str | None:
    """Prefer direct hardware MIDI input before creating a virtual monitor port."""
    hints = [
        h.strip()
        for h in os.environ.get(
            "MIDICRT_INPUT_HINTS",
            "USB MIDI Interface,Cirklon,USB MIDI,MIDI 1",
        ).split(",")
        if h.strip()
    ]
    try:
        names = list(mido.get_input_names())
    except Exception:
        return None
    if not names:
        return None

    for hint in hints:
        hint_l = hint.lower()
        for name in names:
            low = name.lower()
            if "greencrt monitor" in low:
                continue
            if hint_l in low:
                return name
    return None


def _append_runtime_log(message: str):
    log_path = os.path.join(os.path.dirname(__file__), "log.txt")
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass


def trigger_capture_recent(trigger: str = "key", bars: int | None = None):
    if not ENGINE:
        return False, "capture failed: engine unavailable", None
    try:
        ok, message, out_path = ENGINE.capture_recent_to_file(bars=bars, trigger=trigger)
    except Exception as exc:
        ok, message, out_path = False, f"capture failed: {exc}", None
    status = f"[Capture] {message}"
    _log_autoconnect(status)
    _append_runtime_log(status)
    sysex_tag = "sx:04" if trigger.startswith("sysex") else "cap"
    global sysex_status, sysex_status_time
    sysex_status = f"{sysex_tag} {'ok' if ok else 'fail'}"
    sysex_status_time = time.time()
    try:
        ENGINE.set_status_text(message)
    except Exception:
        pass
    return ok, message, out_path

#keyboard
def keyboard_listener():
    global exit_flag
    # find plugins of interest once at startup
    _ss = _screensaver_module()
    _pc = _pagecycle_module()
    with term.cbreak():
        while not exit_flag:
            key = term.inkey(timeout=0.05)
            if not key:
                continue

            # wake from screensaver; swallow the keypress
            if _ss and _ss.is_active():
                _ss.deactivate()
                # In compositor mode, screensaver "active" may be set even
                # though blanking is skipped; do not force a double-tap.
                if _compositor is None:
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
            elif key == "\x03":
                # In some tty modes Ctrl-C arrives as a literal keypress
                # instead of SIGINT. Treat it as quit for reliable restarts.
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
            elif key == "^":
                switch_page(16)
                continue
            elif key == "&":
                switch_page(17)
                continue
            elif key.lower() == "t":
                switch_page(10)
                continue
            elif key == "C":
                trigger_capture_recent(trigger="key")
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




def main(profile="run_tui"):
    configure_startup_profile(profile)
    print(f"[Info] Startup profile: {ACTIVE_PROFILE} ({ACTIVE_RENDER_BACKEND})")
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

    midi_in_port = None
    try:
        midi_in_port, selected_input_name, selected_mode = _open_preferred_input()
        print(f"[Info] Listening on '{selected_input_name}' ({selected_mode})")
        autoconnect_panic_output()
        threading.Thread(target=_panic_autoconnect_retry_loop, daemon=True, name="panic-autoconnect-retry").start()

        threading.Thread(target=ui_loop, daemon=True).start()
        threading.Thread(target=keyboard_listener, daemon=True).start()

        ENGINE.run_input_loop(
            midi_in_port,
            lambda: exit_flag,
            reopen_port=_reopen_input_port,
            on_port_status=lambda msg: (_log_autoconnect(msg), _append_runtime_log(msg)),
        )
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
        # Ensure descriptor cleanup on shutdown.
        _close_port_safely(midi_in_port)
        sys.stdout.write(term.normal)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="midicrt startup profiles")
    parser.add_argument(
        "--profile",
        choices=["run_tui", "run_pixel", "run_compositor"],
        default="run_tui",
        help="Startup profile (default: run_tui)",
    )
    args = parser.parse_args()
    main(profile=args.profile)
