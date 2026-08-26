"""Touch controller surface: /control (UI) + schema/set/action API.

Schema-driven: GET /api/control/schema describes every device's groups,
fields, and actions; the single generic UI in static/control.html renders
from it, so adding a controller = adding a devices/<dev>.py definition and a
DeviceSession subclass here.  Mutations POST under /api/manage/control/*
(the observer's method guard allows POST only below /api/manage/).

MIDI goes straight out this process's own output port (ALSA multiplexes the
UX16 fine alongside midicrt's CRT pages).  Shadow state is in-memory and
per-process — the CRT page and web page don't sync yet (both are write-only
views of a write-only synth; documented limitation).
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any

from devices import lxp1 as LXP1
from devices import matrix1000 as M1K
from devices import tg77 as TG77
from devices import bassstation as BSR

_OUT_HINTS = ["UX16", "USB MIDI", "MIDI 1"]


class MidiOut:
    """Lazily-opened persistent mido output with hint matching + self-heal."""

    def __init__(self, hints=None):
        self._hints = hints or _OUT_HINTS
        self._port = None
        self._lock = threading.Lock()

    def _ensure(self):
        if self._port is not None:
            return self._port
        import mido
        for hint in self._hints:
            hl = hint.lower()
            for name in mido.get_output_names():
                if hl in name.lower():
                    self._port = mido.open_output(name)
                    return self._port
        raise RuntimeError(f"no MIDI output matching {self._hints}")

    def sysex(self, payload):
        import mido
        with self._lock:
            try:
                self._ensure().send(mido.Message("sysex", data=list(payload)))
            except Exception:
                self._port = None
                self._ensure().send(mido.Message("sysex", data=list(payload)))

    def cc(self, channel, control, value):
        import mido
        with self._lock:
            try:
                self._ensure().send(mido.Message(
                    "control_change", channel=(channel - 1) & 0x0F,
                    control=control, value=value))
            except Exception:
                self._port = None
                self._ensure().send(mido.Message(
                    "control_change", channel=(channel - 1) & 0x0F,
                    control=control, value=value))

    def program_change(self, channel, program):
        import mido
        with self._lock:
            self._ensure().send(mido.Message(
                "program_change", channel=(channel - 1) & 0x0F,
                program=program & 0x7F))

    def raw(self, msg_type, channel, **kw):
        import mido
        with self._lock:
            self._ensure().send(mido.Message(
                msg_type, channel=(channel - 1) & 0x0F, **kw))


def _settings_section(settings_path: str, section: str) -> dict:
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            return json.load(f).get(section, {}) or {}
    except Exception:
        return {}


class Lxp1Session:
    def __init__(self, out: MidiOut, settings_path: str):
        cfg = _settings_section(settings_path, "lxp1")
        self.out = out
        self.channel = int(cfg.get("channel", LXP1.DEFAULT_CHANNEL))
        self.klass = str(cfg.get("param_class", "packed"))
        self.preset = int(cfg.get("preset", 0))
        self.program = LXP1.PRESETS[self.preset][1]
        self.values: dict[str, int] = {
            f"p{k}": v for k, v in LXP1.factory_default_steps(self.preset).items()}

    def schema(self) -> dict:
        fields = []
        for p in LXP1.fields_for_program(self.program):
            key = f"p{p['num']}"
            fields.append({
                "key": key, "label": p["name"], "type": "step",
                "min": 0, "max": p["steps"] - 1,
                "value": self.values.get(key),
                "bipolar": p["bipolar"], "unit": p["unit"],
                "dmin": p["dmin"], "dmax": p["dmax"],
            })
        return {
            "id": "lxp1", "name": "LXP-1 (Lexicon reverb)",
            "channel": self.channel,
            "header": f"preset {self.preset}: {LXP1.PRESETS[self.preset][0]}"
                      f" — {LXP1.ALGORITHMS[self.program][0]} [{self.klass}]",
            "legend": "sliders send live · Preset = register 0-15 · Store/Recall = registers 0-127"
                      " · Unit-channel burst: hold the MIDI button while pressing",
            "groups": [{"name": LXP1.ALGORITHMS[self.program][0], "fields": fields}],
            "actions": [
                {"key": "preset", "label": "Preset", "type": "select",
                 "options": [f"{i}: {n}" for i, (n, _) in enumerate(LXP1.PRESETS)],
                 "value": self.preset},
                {"key": "recall", "label": "Recall reg 0-127", "type": "number",
                 "min": 0, "max": 127},
                {"key": "store", "label": "Store to reg", "type": "number",
                 "min": 0, "max": 127},
                {"key": "channel", "label": "MIDI channel", "type": "number",
                 "min": 1, "max": 16, "value": self.channel},
                {"key": "pin_channel", "label": "Unit-channel burst (hold MIDI btn)",
                 "type": "button"},
            ],
        }

    def set_field(self, key: str, value: int):
        num = int(key[1:])
        params = [p for p in LXP1.fields_for_program(self.program) if p["num"] == num]
        if not params:
            raise ValueError(f"unknown field {key}")
        p = params[0]
        step = max(0, min(p["steps"] - 1, int(value)))
        self.values[key] = step
        self.out.sysex(LXP1.param_adjust_sysex(
            num, LXP1.step_to_value16(p, step), self.channel, self.klass))
        return step

    def action(self, key: str, arg: Any):
        if key == "preset":
            self.preset = int(arg) % 16
            self.program = LXP1.PRESETS[self.preset][1]
            self.values = {f"p{k}": v for k, v in LXP1.factory_default_steps(self.preset).items()}
            # presets = rebuilt registers 0-15 (unit's preset table is dead)
            self.out.program_change(self.channel, self.preset)
        elif key == "recall":
            self.out.program_change(self.channel, int(arg))
            self.values.clear()
        elif key == "store":
            self.out.sysex(LXP1.event_sysex(LXP1.EVENT_STORE_REGISTER, int(arg), self.channel))
        elif key == "channel":
            self.channel = max(1, min(16, int(arg)))
        elif key == "pin_channel":
            self.out.raw("note_on", self.channel, note=60, velocity=1)
            self.out.raw("note_off", self.channel, note=60, velocity=0)
            self.out.raw("pitchwheel", self.channel, pitch=0)
        else:
            raise ValueError(f"unknown action {key}")


class Matrix1000Session:
    def __init__(self, out: MidiOut, settings_path: str):
        cfg = _settings_section(settings_path, "matrix1000")
        self.out = out
        self.channel = int(cfg.get("channel", M1K.DEFAULT_CHANNEL))
        self.bank = int(cfg.get("bank", 0))
        self.program = int(cfg.get("program", 0))
        self.values: dict[str, int] = {}
        self.mod: dict[int, list[int]] = {}

    def schema(self) -> dict:
        groups = []
        for g in M1K.groups():
            fields = []
            for (_, pid, name, num, mn, mx, dflt, ck) in M1K.params_in_group(g):
                key = f"n{num}"
                fields.append({
                    "key": key, "label": name,
                    "type": "choice" if ck else "int",
                    "min": mn, "max": mx,
                    "value": self.values.get(key, dflt),
                    "choices": M1K.CHOICES.get(ck) if ck else None,
                })
            groups.append({"name": g, "fields": fields})
        modf = []
        for slot in range(M1K.MOD_SLOTS):
            row = self.mod.get(slot, [0, 0, 0])
            modf.append({"key": f"m{slot}.0", "label": f"M{slot+1} Source",
                         "type": "choice", "min": 0, "max": len(M1K.MOD_SOURCES) - 1,
                         "value": row[0], "choices": M1K.MOD_SOURCES})
            modf.append({"key": f"m{slot}.1", "label": f"M{slot+1} Amount",
                         "type": "int", "min": -63, "max": 63, "value": row[1],
                         "choices": None})
            modf.append({"key": f"m{slot}.2", "label": f"M{slot+1} Dest",
                         "type": "choice", "min": 0, "max": len(M1K.MOD_DESTS) - 1,
                         "value": row[2], "choices": M1K.MOD_DESTS})
        groups.append({"name": "Mod Matrix", "fields": modf})
        return {
            "id": "matrix1000", "name": "Matrix-1000 (Oberheim)",
            "channel": self.channel,
            "header": f"bank {self.bank}  program {self.program:02d}",
            "legend": "sliders send NRPN live (paced) · Bank sends select+unlock · Program = patch 0-99"
                      " · Store writes edit buffer to bank/prog · Mod Matrix at the bottom",
            "groups": groups,
            "actions": [
                {"key": "bank", "label": "Bank 0-9", "type": "number",
                 "min": 0, "max": 9, "value": self.bank},
                {"key": "program", "label": "Program 0-99", "type": "number",
                 "min": 0, "max": 99, "value": self.program},
                {"key": "store", "label": "Store edit buf -> prog", "type": "number",
                 "min": 0, "max": 99},
                {"key": "pull", "label": "Request edit buffer", "type": "button"},
                {"key": "channel", "label": "MIDI channel", "type": "number",
                 "min": 1, "max": 16, "value": self.channel},
            ],
        }

    def set_field(self, key: str, value: int):
        if key.startswith("m"):
            slot_s, role_s = key[1:].split(".")
            slot, role = int(slot_s), int(role_s)
            row = self.mod.setdefault(slot, [0, 0, 0])
            lim = (0, len(M1K.MOD_SOURCES) - 1) if role == 0 else \
                  (-63, 63) if role == 1 else (0, len(M1K.MOD_DESTS) - 1)
            row[role] = max(lim[0], min(lim[1], int(value)))
            self.out.sysex(M1K.mod_matrix_sysex(slot, row[0], row[1], row[2]))
            return row[role]
        num = int(key[1:])
        rows = [r for r in M1K.PARAMS if r[3] == num]
        if not rows:
            raise ValueError(f"unknown field {key}")
        _, _, _, _, mn, mx, _, _ = rows[0]
        v = max(mn, min(mx, int(value)))
        self.values[key] = v
        for cc, val in M1K.nrpn_cc_messages(num, v, self.channel):
            self.out.cc(self.channel, cc, val)
        return v

    def action(self, key: str, arg: Any):
        if key == "bank":
            self.bank = max(0, min(9, int(arg)))
            self.out.sysex(M1K.bank_select_sysex(self.bank))
            self.out.sysex(M1K.bank_unlock_sysex())
        elif key == "program":
            self.program = max(0, min(99, int(arg)))
            self.out.program_change(self.channel, self.program)
            self.values.clear()
        elif key == "store":
            self.out.sysex(M1K.store_edit_buffer_sysex(int(arg), self.bank))
            self.out.sysex(M1K.bank_unlock_sysex())
        elif key == "pull":
            self.out.sysex(M1K.request_edit_buffer_sysex())
        elif key == "channel":
            self.channel = max(1, min(16, int(arg)))
        else:
            raise ValueError(f"unknown action {key}")


class Tg77Session:
    _SIGN = ["+", "-"]

    def __init__(self, out: MidiOut, settings_path: str):
        cfg = _settings_section(settings_path, "tg77")
        self.out = out
        self.channel = int(cfg.get("channel", TG77.DEFAULT_CHANNEL))
        self.device_number = int(cfg.get("device_number", TG77.DEFAULT_DEVICE_NUMBER))
        self.element_slot = int(cfg.get("element_slot", 0))
        self.filter_select = int(cfg.get("filter_select", 0))
        self.values: dict[str, int] = {}
        self._specs = {}
        for spec in (list(TG77.OPERATOR_SPECS) + list(TG77.GLOBAL_SPECS)
                     + list(TG77.FILTER_BANK_SPECS) + list(TG77.FILTER_COMMON_SPECS)
                     + list(TG77.SETUP_SPECS) + list(TG77.AWM_SPECS)):
            self._specs[spec[0]] = spec

    def _families(self):
        fam = {}
        for s in TG77.OPERATOR_SPECS:
            fam[s[0]] = "op"
        for s in TG77.FILTER_BANK_SPECS:
            fam[s[0]] = "flt"
        return fam

    def _skey(self, sid, op=None):
        fam = self._families().get(sid, "g")
        if fam == "op":
            return f"o{op}.{sid}"
        if fam == "flt":
            return f"f{self.filter_select}.{sid}"
        return f"g.{sid}"

    def _get(self, sid, op=None):
        return int(self.values.get(self._skey(sid, op), self._specs[sid][4]))

    def _spec_field(self, spec, key):
        sid, label, mn, mx, dflt, choices = spec
        if choices is None and sid.lower().endswith("sign"):
            choices = self._SIGN
        return {"key": key, "label": label,
                "type": "choice" if choices else "int",
                "min": mn, "max": mx,
                "value": self.values.get(key, dflt),
                "choices": list(choices) if choices else None}

    def schema(self) -> dict:
        groups = []
        for op in range(6):
            groups.append({"name": f"OP{op+1}", "fields": [
                self._spec_field(s, f"o{op}.{s[0]}") for s in TG77.OPERATOR_SPECS]})
        groups.append({"name": "Global", "fields": [
            self._spec_field(s, f"g.{s[0]}") for s in TG77.GLOBAL_SPECS]})
        groups.append({"name": f"Filter (bank {self.filter_select+1})", "fields":
            [self._spec_field(s, f"f{self.filter_select}.{s[0]}") for s in TG77.FILTER_BANK_SPECS]
            + [self._spec_field(s, f"g.{s[0]}") for s in TG77.FILTER_COMMON_SPECS]})
        groups.append({"name": "Setup", "fields": [
            self._spec_field(s, f"g.{s[0]}") for s in TG77.SETUP_SPECS]})
        groups.append({"name": "AWM", "fields": [
            self._spec_field(s, f"g.{s[0]}") for s in TG77.AWM_SPECS]})
        return {
            "id": "tg77", "name": "TG77 (Yamaha)",
            "channel": self.channel,
            "header": f"dev {self.device_number}  slot {self.element_slot+1}"
                      f"  filter bank {self.filter_select+1}",
            "legend": "OP1-6 = AFM operators for the selected element slot · Filter bank 1/2 via action bar"
                      " · Panel Cancel/Exit = front-panel buttons · device number must match the unit",
            "groups": groups,
            "actions": [
                {"key": "slot", "label": "Element slot 1-4", "type": "number",
                 "min": 1, "max": 4, "value": self.element_slot + 1},
                {"key": "filter_select", "label": "Filter bank 1-2", "type": "number",
                 "min": 1, "max": 2, "value": self.filter_select + 1},
                {"key": "device_number", "label": "Device number 0-15", "type": "number",
                 "min": 0, "max": 15, "value": self.device_number},
                {"key": "cancel", "label": "Panel Cancel", "type": "button"},
                {"key": "exit", "label": "Panel Exit", "type": "button"},
                {"key": "channel", "label": "MIDI channel", "type": "number",
                 "min": 1, "max": 16, "value": self.channel},
            ],
        }

    def set_field(self, key: str, value: int):
        if "." not in key:
            raise ValueError(f"bad key {key}")
        scope, sid = key.split(".", 1)
        if sid not in self._specs:
            raise ValueError(f"unknown spec {sid}")
        op = int(scope[1:]) if scope.startswith("o") else None
        mn, mx = self._specs[sid][2], self._specs[sid][3]
        v = max(mn, min(mx, int(value)))
        self.values[key] = v
        msgs = TG77.build_messages(
            sid, lambda s: self._get(s, op),
            op=op, element_slot=self.element_slot,
            filter_select=self.filter_select, device_number=self.device_number)
        for m in msgs:
            self.out.sysex(m)
        return v

    def action(self, key: str, arg: Any):
        if key == "slot":
            self.element_slot = max(0, min(3, int(arg) - 1))
        elif key == "filter_select":
            self.filter_select = max(0, min(1, int(arg) - 1))
        elif key == "device_number":
            self.device_number = max(0, min(15, int(arg)))
        elif key == "cancel":
            for m in TG77.panel_cancel(self.device_number):
                self.out.sysex(m)
        elif key == "exit":
            for m in TG77.panel_exit(self.device_number):
                self.out.sysex(m)
        elif key == "channel":
            self.channel = max(1, min(16, int(arg)))
        else:
            raise ValueError(f"unknown action {key}")


class BassStationSession:
    def __init__(self, out: MidiOut, settings_path: str):
        cfg = _settings_section(settings_path, "bassstation")
        self.out = out
        self.channel = int(cfg.get("channel", BSR.DEFAULT_CHANNEL))
        self.program = int(cfg.get("program", 0))
        self.values: dict[str, int] = {}

    def schema(self) -> dict:
        groups = []
        for g in BSR.groups():
            groups.append({"name": g, "fields": [
                {"key": pid, "label": label, "type": "int",
                 "min": 0, "max": 127,
                 "value": self.values.get(pid), "choices": None}
                for _, pid, label, cc in BSR.params_in_group(g)]})
        return {
            "id": "bassstation", "name": "BassStation (Novation rack)",
            "channel": self.channel,
            "header": f"program {self.program:02d}: {BSR.program_name(self.program)}",
            "legend": "filter + envelopes are the rack's whole MIDI surface (osc/LFO are panel-only)"
                      " · Program 0-39 factory, 40-99 user",
            "groups": groups,
            "actions": [
                {"key": "program", "label": "Program 0-99", "type": "number",
                 "min": 0, "max": 99, "value": self.program},
                {"key": "channel", "label": "MIDI channel", "type": "number",
                 "min": 1, "max": 16, "value": self.channel},
            ],
        }

    def set_field(self, key: str, value: int):
        cc = BSR.cc_for(key)
        v = max(0, min(127, int(value)))
        self.values[key] = v
        self.out.cc(self.channel, cc, v)
        return v

    def action(self, key: str, arg: Any):
        if key == "program":
            self.program = max(0, min(99, int(arg)))
            self.out.program_change(self.channel, self.program)
        elif key == "channel":
            self.channel = max(1, min(16, int(arg)))
        else:
            raise ValueError(f"unknown action {key}")


def register_control_routes(app, settings_path: str) -> None:
    from aiohttp import web

    out = MidiOut()
    sessions = {
        "lxp1": Lxp1Session(out, settings_path),
        "matrix1000": Matrix1000Session(out, settings_path),
        "tg77": Tg77Session(out, settings_path),
        "bassstation": BassStationSession(out, settings_path),
    }

    async def control_page(request):
        page = os.path.join(os.path.dirname(__file__), "static", "control.html")
        return web.FileResponse(page)

    async def get_schema(request):
        return web.json_response({
            "ok": True,
            "devices": [s.schema() for s in sessions.values()],
        })

    def _session(body) -> Any:
        dev = str(body.get("device", ""))
        if dev not in sessions:
            raise ValueError(f"unknown device {dev!r}")
        return sessions[dev]

    async def post_set(request):
        try:
            body = await request.json()
            s = _session(body)
            value = s.set_field(str(body.get("key", "")), int(body.get("value")))
            return web.json_response({"ok": True, "value": value})
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    async def post_action(request):
        try:
            body = await request.json()
            s = _session(body)
            s.action(str(body.get("key", "")), body.get("arg"))
            return web.json_response({"ok": True})
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    app.router.add_get("/control", control_page)
    app.router.add_get("/api/control/schema", get_schema)
    app.router.add_post("/api/manage/control/set", post_set)
    app.router.add_post("/api/manage/control/action", post_action)
