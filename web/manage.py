# web/manage.py
"""Management surface routes: instruments, recordings, sysex librarian.

Registered by web/observer.py onto the same aiohttp app as the read-only
dashboard. Ownership split (see docs/superpowers/specs/2026-08-12-web-
management-design.md): app-state operations go through the IPC command
socket; sysex and recordings operate on this service's own resources.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from aiohttp import web

from web.ipc_client import send_command
from web.sysex_io import SysexLibrary, SysexSender, split_messages

_MAX_NAME_LEN = 32


@dataclass
class ManageDeps:
    settings_path: str
    captures_root: str
    library: SysexLibrary
    sender: SysexSender
    defaults_path: str
    ipc: Callable[..., tuple[bool, dict]] = field(default=send_command)


def _ok(**data: Any) -> web.Response:
    return web.json_response({"ok": True, **data})


def _fail(status: int, error: str) -> web.Response:
    return web.json_response({"ok": False, "error": error}, status=status)


def _ipc_response(ok: bool, data: dict) -> web.Response:
    if ok:
        return _ok(**data)
    message = data.get("error", "command failed")
    status = 503 if "unreachable" in message else 400
    return _fail(status, message)


def register_manage_routes(app: web.Application, deps: ManageDeps) -> None:
    async def get_instruments(request: web.Request) -> web.Response:
        try:
            with open(deps.settings_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            names = cfg.get("instruments", {}).get("names", [])
        except Exception:
            names = []
        return _ok(names=[str(n) for n in names])

    async def post_instruments(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _fail(400, "invalid JSON body")
        names = body.get("names")
        if (not isinstance(names, list) or not names
                or not all(isinstance(n, str) and 0 < len(n) <= _MAX_NAME_LEN for n in names)):
            return _fail(400, f"names must be a non-empty list of strings <= {_MAX_NAME_LEN} chars")
        ok, data = await asyncio.to_thread(
            deps.ipc, "set_config", {"section": "instruments", "value": {"names": names}})
        return _ipc_response(ok, data)

    async def post_capture(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        payload = {}
        if body.get("bars") is not None:
            payload["bars"] = body["bars"]
        ok, data = await asyncio.to_thread(deps.ipc, "capture_recent", payload)
        return _ipc_response(ok, data)

    app.router.add_get("/api/manage/instruments", get_instruments)
    app.router.add_post("/api/manage/instruments", post_instruments)
    app.router.add_post("/api/manage/capture", post_capture)
