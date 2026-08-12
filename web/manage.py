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
import re as _re
import shutil
from dataclasses import dataclass, field
from typing import Any, Callable

from aiohttp import web

from web.ipc_client import send_command
from web.sysex_io import SysexLibrary, SysexSender, split_messages

_MAX_NAME_LEN = 32
_SAFE_SEGMENT = _re.compile(r"^[A-Za-z0-9._ -]+$")


def _resolve_capture_path(root: str, rel: str) -> str:
    """Resolve a captures-relative path, rejecting traversal."""
    parts = [p for p in rel.split("/") if p]
    if not parts or not all(_SAFE_SEGMENT.match(p) and p not in (".", "..") for p in parts):
        raise ValueError("invalid path")
    path = os.path.abspath(os.path.join(root, *parts))
    root_abs = os.path.abspath(root)
    if not (path == root_abs or path.startswith(root_abs + os.sep)):
        raise ValueError("path escapes captures root")
    return path


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

    def _recording_entries() -> list[dict]:
        root = deps.captures_root
        entries: list[dict] = []

        def add(rel: str, path: str):
            note_path = (os.path.join(path, "note.txt") if os.path.isdir(path)
                         else path + ".note.txt")
            note = ""
            if os.path.isfile(note_path):
                note = open(note_path, "r", encoding="utf-8").read().strip()
            if os.path.isdir(path):
                files = sorted(os.listdir(path))
                size = sum(os.path.getsize(os.path.join(path, f))
                           for f in files if os.path.isfile(os.path.join(path, f)))
            else:
                files = []
                size = os.path.getsize(path)
            entries.append({"path": rel, "bytes": size, "mtime": os.path.getmtime(path),
                            "note": note, "files": files,
                            "kind": "dir" if os.path.isdir(path) else "file"})

        if os.path.isdir(root):
            for fn in sorted(os.listdir(root)):
                if fn == "pianoroll_exp" or fn.endswith(".note.txt"):
                    continue
                add(fn, os.path.join(root, fn))
        sessions = os.path.join(root, "pianoroll_exp", "sessions")
        if os.path.isdir(sessions):
            for fn in sorted(os.listdir(sessions)):
                if fn.endswith(".note.txt"):
                    continue
                add(f"pianoroll_exp/sessions/{fn}", os.path.join(sessions, fn))
        return entries

    async def get_recordings(request: web.Request) -> web.Response:
        return _ok(entries=_recording_entries())

    async def download_recording(request: web.Request) -> web.Response:
        rel = request.query.get("path", "")
        try:
            path = _resolve_capture_path(deps.captures_root, rel)
        except ValueError as exc:
            return _fail(400, str(exc))
        if not os.path.isfile(path):
            return _fail(404, "no such file")
        return web.FileResponse(path)

    async def delete_recording(request: web.Request) -> web.Response:
        body = await request.json()
        try:
            path = _resolve_capture_path(deps.captures_root, str(body.get("path", "")))
        except ValueError as exc:
            return _fail(400, str(exc))
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.isfile(path):
            os.remove(path)
        else:
            return _fail(404, "no such recording")
        return _ok()

    async def rename_recording(request: web.Request) -> web.Response:
        body = await request.json()
        new_name = str(body.get("new_name", ""))
        try:
            path = _resolve_capture_path(deps.captures_root, str(body.get("path", "")))
            if not _SAFE_SEGMENT.match(new_name):
                raise ValueError("invalid new_name")
            target = os.path.join(os.path.dirname(path), new_name)
        except ValueError as exc:
            return _fail(400, str(exc))
        if not os.path.exists(path):
            return _fail(404, "no such recording")
        if os.path.exists(target):
            return _fail(400, "target already exists")
        os.rename(path, target)
        return _ok()

    async def note_recording(request: web.Request) -> web.Response:
        body = await request.json()
        try:
            path = _resolve_capture_path(deps.captures_root, str(body.get("path", "")))
        except ValueError as exc:
            return _fail(400, str(exc))
        if not os.path.exists(path):
            return _fail(404, "no such recording")
        note_path = (os.path.join(path, "note.txt") if os.path.isdir(path)
                     else path + ".note.txt")
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(str(body.get("note", "")))
        return _ok()

    app.router.add_get("/api/manage/instruments", get_instruments)
    app.router.add_post("/api/manage/instruments", post_instruments)
    app.router.add_post("/api/manage/capture", post_capture)
    app.router.add_get("/api/manage/recordings", get_recordings)
    app.router.add_get("/api/manage/recordings/download", download_recording)
    app.router.add_post("/api/manage/recordings/delete", delete_recording)
    app.router.add_post("/api/manage/recordings/rename", rename_recording)
    app.router.add_post("/api/manage/recordings/note", note_recording)
