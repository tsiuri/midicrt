# plugins/midimap.py — external-controller translation layer (runs in the
# background regardless of the current page).
#
# Plain MIDI from the Cirklon / any controller arriving on midicrt's monitor
# input (CC, or NRPN via CC99/98/6/38) is matched against the persistent
# mappings in settings.json "midimap" and translated into each device's
# native edit messages through the same DeviceSession encoders the web
# surface uses (own output port; paced for slow synths).  The page that owns
# the field gets on_mapped_set(key, value) so the CRT/shadow follows.
#
# Learn: a page calls arm_learn(device, key, label); the next CC/NRPN seen
# binds to that field (20 s timeout).  unbind(device, key) removes it.

import threading
import time

import midicrt
from configutil import load_section, save_section
from midimap_model import MapModel, NrpnAssembler, scale_to_field, describe_src, SECTION

_cfg = {}
try:
    _cfg = load_section(SECTION) or {}
except Exception:
    _cfg = {}

MODEL = MapModel(_cfg.get("mappings", []))
_nrpn = NrpnAssembler()
_lock = threading.Lock()
_learn = None          # {"device","key","label","at"}
LEARN_TIMEOUT_S = 20.0
_sessions = None
_field_cache = {}      # (device,key) -> (min,max,ts)
_stats = {"matched": 0, "sent": 0, "last": ""}

# pacing: slow synths (Matrix-1000) choke on fast streams; coalesce per
# device+key and flush from a ticker thread, trailing value always wins.
_MIN_GAP = {"matrix1000": 0.035, "tg77": 0.02, "lxp1": 0.01, "bassstation": 0.0}
_pending = {}          # (device,key) -> value
_last_tx = {}          # device -> ts


def _save():
    try:
        save_section(SECTION, MODEL.to_config())
    except Exception:
        pass


def _get_sessions():
    global _sessions
    if _sessions is None:
        from web.control import (MidiOut, Lxp1Session, Matrix1000Session,
                                 Tg77Session, BassStationSession)
        import os
        settings_path = os.path.join(os.path.dirname(midicrt.__file__), "config", "settings.json")
        out = MidiOut()
        _sessions = {
            "lxp1": Lxp1Session(out, settings_path),
            "matrix1000": Matrix1000Session(out, settings_path),
            "tg77": Tg77Session(out, settings_path),
            "bassstation": BassStationSession(out, settings_path),
        }
    return _sessions


def _field_range(device, key):
    now = time.time()
    hit = _field_cache.get((device, key))
    if hit and now - hit[2] < 2.0:
        return hit[0], hit[1]
    sess = _get_sessions().get(device)
    if sess is None:
        return None
    for g in sess.schema()["groups"]:
        for f in g["fields"]:
            if f["key"] == key:
                _field_cache[(device, key)] = (f["min"], f["max"], now)
                return f["min"], f["max"]
    return None


def _page_for(device):
    for p in midicrt.PAGES.values():
        if getattr(p, "DEVICE_ID", None) == device:
            return p
    return None


# --- public API for pages ----------------------------------------------------

def arm_learn(device, key, label=""):
    global _learn
    with _lock:
        _learn = {"device": device, "key": key, "label": label, "at": time.time()}


def learn_status():
    with _lock:
        if _learn and time.time() - _learn["at"] <= LEARN_TIMEOUT_S:
            return f"MAP LEARN: move a controller for {_learn['label'] or _learn['key']}"
    return ""


def describe(device, key):
    return MODEL.describe(device, key)


def unbind(device, key):
    if MODEL.unbind(device, key):
        _save()
        return True
    return False


def stats():
    return dict(_stats)


# --- translation ----------------------------------------------------------

def _apply(device, key, value, in_max):
    rng = _field_range(device, key)
    if rng is None:
        return
    v = scale_to_field(value, in_max, rng[0], rng[1])
    _pending[(device, key)] = v
    _stats["matched"] += 1


def _dispatch(device, key, v):
    sess = _get_sessions().get(device)
    if sess is None:
        return
    try:
        v = sess.set_field(key, v)
        _stats["sent"] += 1
        _stats["last"] = f"{device}:{key}={v}"
    except Exception as exc:
        _stats["last"] = f"ERR {device}:{key} {exc}"
        return
    page = _page_for(device)
    fn = getattr(page, "on_mapped_set", None)
    if fn:
        try:
            fn(key, v)
        except Exception:
            pass


def _flusher():
    while True:
        now = time.time()
        for dk in list(_pending.keys()):
            device = dk[0]
            gap = _MIN_GAP.get(device, 0.0)
            if now - _last_tx.get(device, 0.0) >= gap:
                v = _pending.pop(dk, None)
                if v is not None:
                    _last_tx[device] = now
                    _dispatch(dk[0], dk[1], v)
        time.sleep(0.005)


threading.Thread(target=_flusher, name="midimap-flush", daemon=True).start()


def _consider(src_type, ch, num, value, bits):
    global _learn
    with _lock:
        if _learn and time.time() - _learn["at"] > LEARN_TIMEOUT_S:
            _learn = None
        if _learn:
            MODEL.bind(_learn["device"], _learn["key"], {"type": src_type, "ch": ch, "num": num})
            _save()
            _field_cache.clear()
            _learn = None
            return
    in_max = (1 << bits) - 1
    for m in MODEL.targets_for(src_type, ch, num):
        _apply(m["device"], m["key"], value, in_max)


# Extra inputs: controllers usually arrive on their own interface (a second
# USB MIDI cable on the Pi, the Cirklon's port...). settings "midimap":
# {"input_hints": ["Cirklon", "USB MIDI"]} — each matching port is opened
# and fed through handle() exactly like the monitor input.  Never list the
# rack return interface here (it's already the monitor input).
_extra_hints = list(_cfg.get("input_hints", []))
_extra_ports = {}


def _extra_worker():
    import mido
    while True:
        try:
            names = list(mido.get_input_names())
        except Exception:
            names = []
        for hint in _extra_hints:
            hl = str(hint).lower()
            for n in names:
                if hl in n.lower() and n not in _extra_ports:
                    try:
                        _extra_ports[n] = mido.open_input(n)
                    except Exception:
                        pass
        for n, port in list(_extra_ports.items()):
            try:
                for m in port.iter_pending():
                    handle(m)
            except Exception:
                try:
                    port.close()
                except Exception:
                    pass
                _extra_ports.pop(n, None)
        time.sleep(0.01 if _extra_ports else 3.0)


if _extra_hints:
    threading.Thread(target=_extra_worker, name="midimap-inputs", daemon=True).start()


def handle(msg):
    if msg.type != "control_change":
        return
    ch = msg.channel + 1
    cc = msg.control
    if cc in (99, 98, 6, 38):
        res = _nrpn.feed(ch, cc, msg.value)
        if res:
            param, value, bits = res
            _consider("nrpn", ch, param, value, bits)
        return
    _consider("cc", ch, cc, msg.value, 7)
