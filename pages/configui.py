# pages/configui.py — Config editor (auto-discover settings.json)
BACKGROUND = True
PAGE_ID = 14
PAGE_NAME = "Config"

import os
import time
from midicrt import draw_line, term
from configutil import load_settings, save_settings, config_path
from pages.legacy_contract_bridge import build_widget_from_legacy_contract

_ROOT = {}
_PATH = []  # list of keys/indices
_SELECTED = 0
_SCROLL = 0
_EDIT_MODE = False
_EDIT_BUFFER = ""
_EDIT_TARGET = None
_DIRTY = False
_LAST_SAVE = 0.0
_LAST_MTIME = 0.0
_LAST_ADJUST_TIME = 0.0
_ADJUST_STREAK = 0
_LAST_ADJUST_KEY = None
_ACCEL_STEP_INT = 2
_ACCEL_STEP_FLOAT = 0.1
_ACCEL_WINDOW = 0.25
_ACCEL_MAX = 10
_CONFIGUI_BOOTSTRAP = False


def _load_if_changed(force=False):
    global _ROOT, _LAST_MTIME
    path = config_path()
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        mtime = 0.0
    if force or (mtime != _LAST_MTIME and not _EDIT_MODE):
        _ROOT = load_settings()
        _apply_config_section()
        global _CONFIGUI_BOOTSTRAP, _DIRTY
        if not _CONFIGUI_BOOTSTRAP:
            if _ensure_config_section():
                _DIRTY = True
            _CONFIGUI_BOOTSTRAP = True
        _LAST_MTIME = mtime


def _apply_config_section():
    global _ACCEL_STEP_INT, _ACCEL_STEP_FLOAT, _ACCEL_WINDOW, _ACCEL_MAX
    cfg = _ROOT.get("configui") if isinstance(_ROOT, dict) else None
    if not isinstance(cfg, dict):
        return
    try:
        _ACCEL_STEP_INT = int(cfg.get("accel_step_int", _ACCEL_STEP_INT))
        _ACCEL_STEP_FLOAT = float(cfg.get("accel_step_float", _ACCEL_STEP_FLOAT))
        _ACCEL_WINDOW = float(cfg.get("accel_window", _ACCEL_WINDOW))
        _ACCEL_MAX = int(cfg.get("accel_max", _ACCEL_MAX))
    except Exception:
        pass


def _ensure_config_section():
    if not isinstance(_ROOT, dict):
        return False
    cfg = _ROOT.get("configui")
    if not isinstance(cfg, dict):
        cfg = {}
        _ROOT["configui"] = cfg
        changed = True
    else:
        changed = False
    cfg.setdefault("accel_step_int", _ACCEL_STEP_INT)
    cfg.setdefault("accel_step_float", _ACCEL_STEP_FLOAT)
    cfg.setdefault("accel_window", _ACCEL_WINDOW)
    cfg.setdefault("accel_max", _ACCEL_MAX)
    return changed


def _node_at(path):
    node = _ROOT
    for key in path:
        if isinstance(node, dict):
            node = node.get(key)
        elif isinstance(node, list):
            try:
                node = node[int(key)]
            except Exception:
                return None
        else:
            return None
    return node


def _set_at(path, value):
    node = _ROOT
    for key in path[:-1]:
        if isinstance(node, dict):
            node = node.get(key)
        elif isinstance(node, list):
            node = node[int(key)]
        else:
            return False
    last = path[-1]
    if isinstance(node, dict):
        node[last] = value
        return True
    if isinstance(node, list):
        try:
            node[int(last)] = value
            return True
        except Exception:
            return False
    return False


def _entries(node):
    if isinstance(node, dict):
        keys = sorted(node.keys())
        return [(k, node[k]) for k in keys]
    if isinstance(node, list):
        return [(i, node[i]) for i in range(len(node))]
    return []


def _path_label():
    if not _PATH:
        return "/"
    parts = []
    for p in _PATH:
        parts.append(str(p))
    return "/" + "/".join(parts)


def _value_preview(val):
    if isinstance(val, dict):
        return f"<dict {len(val)}>"
    if isinstance(val, list):
        return f"<list {len(val)}>"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, float):
        return f"{val:.4g}"
    return str(val)


def _begin_edit(value, path):
    global _EDIT_MODE, _EDIT_BUFFER, _EDIT_TARGET
    _EDIT_MODE = True
    _EDIT_TARGET = list(path)
    _EDIT_BUFFER = str(value)


def _commit_edit():
    global _EDIT_MODE, _EDIT_BUFFER, _EDIT_TARGET, _DIRTY
    if _EDIT_TARGET is None:
        _EDIT_MODE = False
        return
    raw = _EDIT_BUFFER.strip()
    current = _node_at(_EDIT_TARGET)
    new_val = raw
    try:
        if isinstance(current, bool):
            new_val = raw.lower() in ("1", "true", "yes", "on")
        elif isinstance(current, int):
            new_val = int(raw)
        elif isinstance(current, float):
            new_val = float(raw)
    except Exception:
        new_val = current
    _set_at(_EDIT_TARGET, new_val)
    _DIRTY = True
    _EDIT_MODE = False
    _EDIT_TARGET = None


def _save_if_dirty():
    global _DIRTY, _LAST_SAVE
    if not _DIRTY:
        return
    now = time.time()
    if now - _LAST_SAVE < 0.4:
        return
    _ensure_config_section()
    save_settings(_ROOT)
    _LAST_SAVE = now
    _DIRTY = False


def _adjust_number(val, delta):
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return val + int(delta)
    if isinstance(val, float):
        return val + float(delta)
    return val


def _adjust_step(val, sign, key_id):
    global _LAST_ADJUST_TIME, _ADJUST_STREAK, _LAST_ADJUST_KEY
    now = time.time()
    if _LAST_ADJUST_KEY == key_id and (now - _LAST_ADJUST_TIME) < _ACCEL_WINDOW:
        _ADJUST_STREAK += 1
    else:
        _ADJUST_STREAK = 0
        _LAST_ADJUST_KEY = key_id
    _LAST_ADJUST_TIME = now
    factor = 1 + min(max(0, _ACCEL_MAX - 1), _ADJUST_STREAK // 2)
    if isinstance(val, int):
        return sign * max(1, factor * max(1, _ACCEL_STEP_INT))
    if isinstance(val, float):
        return sign * (_ACCEL_STEP_FLOAT * factor)
    return 0


def keypress(ch):
    global _SELECTED, _SCROLL, _EDIT_MODE, _EDIT_BUFFER, _DIRTY
    s = str(ch)

    if _EDIT_MODE:
        if (ch.is_sequence and ch.name in ("KEY_ESCAPE", "KEY_EXIT")) or s == "\x1b":
            _EDIT_MODE = False
            return True
        if (ch.is_sequence and ch.name in ("KEY_BACKSPACE", "KEY_DELETE")) or s in ("\x7f", "\b"):
            _EDIT_BUFFER = _EDIT_BUFFER[:-1]
            return True
        if (ch.is_sequence and ch.name in ("KEY_ENTER", "KEY_RETURN")) or s in ("\n", "\r"):
            _commit_edit()
            return True
        if s in ("\x03", "\x07"):  # Ctrl-C / Ctrl-G cancels edit mode
            _EDIT_MODE = False
            return True
        if len(s) == 1 and s.isprintable():
            _EDIT_BUFFER += s
            return True
        return True

    node = _node_at(_PATH)
    items = _entries(node)
    total = len(items)
    if s in ("j", "J") or (ch.is_sequence and ch.name == "KEY_DOWN"):
        if total:
            _SELECTED = min(total - 1, _SELECTED + 1)
        return True
    if s in ("k", "K") or (ch.is_sequence and ch.name == "KEY_UP"):
        if total:
            _SELECTED = max(0, _SELECTED - 1)
        return True
    if s in ("\n", "\r") or (ch.is_sequence and ch.name == "KEY_RIGHT"):
        if total:
            key, val = items[_SELECTED]
            if isinstance(val, (dict, list)):
                _PATH.append(key)
                _SELECTED = 0
                _SCROLL = 0
            else:
                _begin_edit(val, _PATH + [key])
        return True
    if ch.is_sequence and ch.name in ("KEY_LEFT", "KEY_BACKSPACE"):
        if _PATH:
            _PATH.pop()
            _SELECTED = 0
            _SCROLL = 0
        return True
    if s in ("+", "="):
        if total:
            key, val = items[_SELECTED]
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                step = _adjust_step(val, 1, "+")
                _set_at(_PATH + [key], _adjust_number(val, step))
                _DIRTY = True
        return True
    if s == "-":
        if total:
            key, val = items[_SELECTED]
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                step = _adjust_step(val, -1, "-")
                _set_at(_PATH + [key], _adjust_number(val, step))
                _DIRTY = True
        return True
    if s == " ":
        if total:
            key, val = items[_SELECTED]
            if isinstance(val, bool):
                _set_at(_PATH + [key], not val)
                _DIRTY = True
        return True
    if s.lower() == "e":
        if total:
            key, val = items[_SELECTED]
            if not isinstance(val, (dict, list)):
                _begin_edit(val, _PATH + [key])
        return True
    if s.lower() == "r":
        _load_if_changed(force=True)
        return True
    if s.lower() == "s":
        save_settings(_ROOT)
        return True

    return False


def draw(state):
    global _SCROLL
    cols = state["cols"]
    rows = state["rows"]
    y0 = state.get("y_offset", 3)

    _load_if_changed()
    _save_if_dirty()

    draw_line(y0, f"--- {PAGE_NAME} ---".ljust(cols))
    draw_line(y0 + 1, f"Path: {_path_label()}"[:cols])
    help1 = "Up/Down select  Enter/Right open  Left back"
    help2 = "+/- adjust  space toggle  e edit  r reload"
    draw_line(y0 + 2, help1[:cols])
    draw_line(y0 + 3, help2[:cols])

    node = _node_at(_PATH)
    items = _entries(node)
    total = len(items)

    list_start = y0 + 4
    list_height = max(1, rows - list_start - 4)

    if _SELECTED < _SCROLL:
        _SCROLL = _SELECTED
    if _SELECTED >= _SCROLL + list_height:
        _SCROLL = _SELECTED - list_height + 1

    for i in range(list_height):
        idx = _SCROLL + i
        y = list_start + i
        if idx >= total:
            draw_line(y, "".ljust(cols))
            continue
        key, val = items[idx]
        prefix = ">" if idx == _SELECTED else " "
        k = str(key)
        v = _value_preview(val)
        line = f"{prefix} {k}: {v}"
        draw_line(y, line[:cols])

    if _EDIT_MODE:
        edit_line = f"Edit: {_EDIT_BUFFER}"
        draw_line(2, edit_line[:cols])


def build_widget(state):
    return build_widget_from_legacy_contract(draw, state, draw_line)
