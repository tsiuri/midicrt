# tests/test_manage_routes.py
import json
import os
import tempfile
import unittest

from aiohttp.test_utils import AioHTTPTestCase
from aiohttp import web as aioweb

from web.manage import ManageDeps, register_manage_routes
from web.sysex_io import SysexLibrary, SysexSender


class _FakeIPC:
    def __init__(self):
        self.calls = []
        self.reply = (True, {"message": "ok"})

    def __call__(self, command, payload=None, **kw):
        self.calls.append((command, payload))
        return self.reply


class _Base(AioHTTPTestCase):
    async def get_application(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.settings_path = os.path.join(root, "settings.json")
        json.dump({"instruments": {"names": ["A", "B"]}}, open(self.settings_path, "w"))
        self.captures = os.path.join(root, "captures")
        os.makedirs(self.captures)
        self.ipc = _FakeIPC()
        deps = ManageDeps(
            settings_path=self.settings_path,
            captures_root=self.captures,
            library=SysexLibrary(os.path.join(root, "sysex_library")),
            sender=SysexSender(backend=_NullBackend()),
            defaults_path=os.path.join(root, "manage-defaults.json"),
            ipc=self.ipc,
        )
        app = aioweb.Application()
        self._deps = deps
        register_manage_routes(app, deps)
        return app

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()


class _NullBackend:
    def get_output_names(self):
        return []

    def open_output(self, name):
        raise ValueError("no ports")


class InstrumentsTest(_Base):
    async def test_get_names(self):
        resp = await self.client.get("/api/manage/instruments")
        body = await resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["names"], ["A", "B"])

    async def test_post_names_goes_via_ipc(self):
        resp = await self.client.post("/api/manage/instruments",
                                      json={"names": ["X", "Y", "Z"]})
        body = await resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(self.ipc.calls,
                         [("set_config", {"section": "instruments",
                                          "value": {"names": ["X", "Y", "Z"]}})])

    async def test_post_rejects_bad_payloads(self):
        for payload in ({"names": []}, {"names": "no"}, {"names": [1, 2]},
                        {"names": ["x" * 40]}, {}):
            resp = await self.client.post("/api/manage/instruments", json=payload)
            self.assertEqual(resp.status, 400)
        self.assertEqual(self.ipc.calls, [])


class CaptureTest(_Base):
    async def test_capture_forwards_to_ipc(self):
        self.ipc.reply = (True, {"message": "captured", "path": "/tmp/c.mid"})
        resp = await self.client.post("/api/manage/capture", json={})
        body = await resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["path"], "/tmp/c.mid")
        self.assertEqual(self.ipc.calls[0][0], "capture_recent")

    async def test_app_down_returns_503(self):
        self.ipc.reply = (False, {"error": "midicrt app unreachable: x"})
        resp = await self.client.post("/api/manage/capture", json={})
        self.assertEqual(resp.status, 503)


class RecordingsTest(_Base):
    def _seed(self):
        d = os.path.join(self.captures, "20260228-200249")
        os.makedirs(d)
        open(os.path.join(d, "capture.mid"), "wb").write(b"MThd" + b"\x00" * 20)
        s = os.path.join(self.captures, "pianoroll_exp", "sessions")
        os.makedirs(s)
        open(os.path.join(s, "engine-memory-abc.json"), "w").write("{}")

    async def test_list_download_note_rename_delete(self):
        self._seed()
        resp = await self.client.get("/api/manage/recordings")
        body = await resp.json()
        names = [e["path"] for e in body["entries"]]
        self.assertIn("20260228-200249", names)
        self.assertIn("pianoroll_exp/sessions/engine-memory-abc.json", names)

        resp = await self.client.get(
            "/api/manage/recordings/download",
            params={"path": "20260228-200249/capture.mid"})
        self.assertEqual(resp.status, 200)
        self.assertTrue((await resp.read()).startswith(b"MThd"))

        resp = await self.client.post("/api/manage/recordings/note",
                                      json={"path": "20260228-200249", "note": "good take"})
        self.assertTrue((await resp.json())["ok"])
        resp = await self.client.get("/api/manage/recordings")
        entry = [e for e in (await resp.json())["entries"]
                 if e["path"] == "20260228-200249"][0]
        self.assertEqual(entry["note"], "good take")

        resp = await self.client.post("/api/manage/recordings/rename",
                                      json={"path": "20260228-200249", "new_name": "good-take"})
        self.assertTrue((await resp.json())["ok"])

        resp = await self.client.post("/api/manage/recordings/delete",
                                      json={"path": "good-take"})
        self.assertTrue((await resp.json())["ok"])
        resp = await self.client.get("/api/manage/recordings")
        self.assertNotIn("good-take", [e["path"] for e in (await resp.json())["entries"]])

    async def test_traversal_rejected(self):
        resp = await self.client.get("/api/manage/recordings/download",
                                     params={"path": "../settings.json"})
        self.assertEqual(resp.status, 400)
        resp = await self.client.post("/api/manage/recordings/delete",
                                      json={"path": "../../etc"})
        self.assertEqual(resp.status, 400)

    async def test_delete_rejects_malformed_json_body(self):
        resp = await self.client.post("/api/manage/recordings/delete",
                                      data="not json", headers={"Content-Type": "application/json"})
        self.assertEqual(resp.status, 400)

    async def test_rename_rejects_dotdot_new_name(self):
        self._seed()
        resp = await self.client.post("/api/manage/recordings/rename",
                                      json={"path": "20260228-200249", "new_name": ".."})
        self.assertEqual(resp.status, 400)


if __name__ == "__main__":
    unittest.main()
