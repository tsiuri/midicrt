# pages/configui.py — Config editor (auto-discover settings.json)
BACKGROUND = True
PAGE_ID = 14
PAGE_NAME = "Config"

import os
import time
from midicrt import draw_line, term
from configutil import load_settings, save_settings, config_path
from ui.model import PageLinesWidget

_ROOT = {}
_EXPANDED: set[tuple] = set()  # tree paths currently expanded inline
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


def _tree_rows():
    """Flatten the config tree into the visible row list.

    Each row is (path_tuple, key, value, depth). A dict/list row whose
    path is in _EXPANDED contributes its children directly beneath it at
    depth + 1 — sections expand in place instead of navigating away.
    """
    rows = []

    def walk(node, path, depth):
        for key, val in _entries(node):
            p = path + (key,)
            rows.append((p, key, val, depth))
            if isinstance(val, (dict, list)) and p in _EXPANDED:
                walk(val, p, depth + 1)

    walk(_ROOT, (), 0)
    return rows


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

    rows = _tree_rows()
    total = len(rows)
    if total:
        _SELECTED = max(0, min(total - 1, _SELECTED))
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
            path, key, val, _depth = rows[_SELECTED]
            if isinstance(val, (dict, list)):
                # Toggle in place; rows above the selection are unaffected,
                # so the selected row keeps pointing at this section.
                if path in _EXPANDED:
                    _EXPANDED.discard(path)
                else:
                    _EXPANDED.add(path)
            else:
                _begin_edit(val, list(path))
        return True
    if ch.is_sequence and ch.name in ("KEY_LEFT", "KEY_BACKSPACE"):
        if total:
            path, key, val, _depth = rows[_SELECTED]
            if isinstance(val, (dict, list)) and path in _EXPANDED:
                _EXPANDED.discard(path)
            elif len(path) > 1:
                # Collapse the parent branch and land the selection on it.
                parent = path[:-1]
                _EXPANDED.discard(parent)
                for i, row in enumerate(_tree_rows()):
                    if row[0] == parent:
                        _SELECTED = i
                        break
        return True
    if s in ("+", "="):
        if total:
            path, key, val, _depth = rows[_SELECTED]
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                step = _adjust_step(val, 1, "+")
                _set_at(list(path), _adjust_number(val, step))
                _DIRTY = True
        return True
    if s == "-":
        if total:
            path, key, val, _depth = rows[_SELECTED]
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                step = _adjust_step(val, -1, "-")
                _set_at(list(path), _adjust_number(val, step))
                _DIRTY = True
        return True
    if s == " ":
        if total:
            path, key, val, _depth = rows[_SELECTED]
            if isinstance(val, bool):
                _set_at(list(path), not val)
                _DIRTY = True
        return True
    if s.lower() == "e":
        if total:
            path, key, val, _depth = rows[_SELECTED]
            if not isinstance(val, (dict, list)):
                _begin_edit(val, list(path))
        return True
    if s.lower() == "r":
        _load_if_changed(force=True)
        return True
    if s.lower() == "s":
        save_settings(_ROOT)
        return True

    return False


def _row_text(path, key, val, depth, selected):
    sel = ">" if selected else " "
    indent = "  " * depth
    if isinstance(val, (dict, list)):
        if path in _EXPANDED:
            return f"{sel} {indent}- {key}:"
        return f"{sel} {indent}+ {key}: {_value_preview(val)}"
    return f"{sel} {indent}  {key}: {_value_preview(val)}"


def _build_widget_lines(state):
    global _SCROLL
    cols = int(state.get("cols", 95))
    rows = int(state.get("rows", 30))
    _load_if_changed()
    _save_if_dirty()
    lines = [
        f"--- {PAGE_NAME} ---",
        f"File: {config_path()}",
        "Up/Down select  Enter/Right expand or edit  Left collapse",
        "+/- adjust  space toggle  e edit  r reload",
    ]
    tree = _tree_rows()
    total = len(tree)
    list_height = max(1, rows - 8)
    if _SELECTED < _SCROLL:
        _SCROLL = _SELECTED
    if _SELECTED >= _SCROLL + list_height:
        _SCROLL = _SELECTED - list_height + 1
    _SCROLL = max(0, min(_SCROLL, max(0, total - list_height)))
    for i in range(list_height):
        idx = _SCROLL + i
        if idx >= total:
            lines.append("")
            continue
        path, key, val, depth = tree[idx]
        lines.append(_row_text(path, key, val, depth, idx == _SELECTED)[:cols])
    if _EDIT_MODE:
        lines.insert(0, f"Edit: {_EDIT_BUFFER}")
    return lines


def draw(state):
    cols = state["cols"]
    rows = state["rows"]
    y0 = state.get("y_offset", 3)
    lines = _build_widget_lines(state)
    y = y0
    for line in lines:
        if y >= rows - 4:
            break
        draw_line(y, line[:cols].ljust(cols))
        y += 1
    while y < rows - 4:
        draw_line(y, " " * cols)
        y += 1


def build_widget(state):
    return PageLinesWidget(page_id=PAGE_ID, page_name=PAGE_NAME, lines=_build_widget_lines(state))
