# configutil.py — shared JSON settings helpers

import json
import os

CONFIG_FILE = "settings.json"


def _config_dir():
    base = os.environ.get("MIDICRT_CONFIG_DIR")
    if not base:
        base = os.path.join(os.path.dirname(__file__), "config")
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        pass
    return base


def config_path():
    return os.path.join(_config_dir(), CONFIG_FILE)


def load_settings():
    path = config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_settings(root):
    try:
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(root, f, indent=2)
    except Exception:
        pass


def load_section(name):
    root = load_settings()
    section = root.get(name)
    return section if isinstance(section, dict) else None


def save_section(name, data):
    root = load_settings()
    if not isinstance(root, dict):
        root = {}
    root[name] = data
    save_settings(root)
