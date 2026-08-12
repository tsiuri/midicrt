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


class ManagePageTest(_Base):
    async def test_manage_page_serves_html(self):
        resp = await self.client.get("/manage")
        self.assertEqual(resp.status, 200)
        self.assertIn("text/html", resp.headers.get("Content-Type", ""))


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

    async def test_corrupt_settings_returns_500(self):
        with open(self.settings_path, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        resp = await self.client.get("/api/manage/instruments")
        self.assertEqual(resp.status, 500)
        body = await resp.json()
        self.assertFalse(body["ok"])
        self.assertIn("settings.json unreadable", body["error"])

    async def test_missing_settings_file_returns_empty_names(self):
        os.remove(self.settings_path)
        resp = await self.client.get("/api/manage/instruments")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["names"], [])


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

    async def test_rename_file_kind_moves_note_sidecar(self):
        open(os.path.join(self.captures, "solo.mid"), "wb").write(b"MThd")
        open(os.path.join(self.captures, "solo.mid.note.txt"), "w").write("nice take")

        resp = await self.client.post("/api/manage/recordings/rename",
                                      json={"path": "solo.mid", "new_name": "solo2.mid"})
        self.assertTrue((await resp.json())["ok"])

        self.assertFalse(os.path.exists(os.path.join(self.captures, "solo.mid.note.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.captures, "solo2.mid.note.txt")))

        resp = await self.client.get("/api/manage/recordings")
        entries = {e["path"]: e for e in (await resp.json())["entries"]}
        self.assertNotIn("solo.mid", entries)
        self.assertEqual(entries["solo2.mid"]["note"], "nice take")

    async def test_delete_file_kind_removes_note_sidecar(self):
        open(os.path.join(self.captures, "solo.mid"), "wb").write(b"MThd")
        open(os.path.join(self.captures, "solo.mid.note.txt"), "w").write("nice take")

        resp = await self.client.post("/api/manage/recordings/delete",
                                      json={"path": "solo.mid"})
        self.assertTrue((await resp.json())["ok"])

        self.assertFalse(os.path.exists(os.path.join(self.captures, "solo.mid")))
        self.assertFalse(os.path.exists(os.path.join(self.captures, "solo.mid.note.txt")))


class _RecordingSendBackend(_NullBackend):
    def __init__(self):
        self.sent = []

    def get_output_names(self):
        return ["Synth A 20:0"]

    def open_output(self, name):
        backend = self

        class _Out:
            def send(self, msg):
                backend.sent.append(bytes(msg.bytes()))

            def close(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.close()
                return False

        return _Out()


class SysexRoutesTest(_Base):
    SYX = bytes([0xF0, 0x41, 0x10, 0xF7])

    async def get_application(self):
        app = await super().get_application()
        # swap in a recording backend for execute tests
        self.send_backend = _RecordingSendBackend()
        self._deps.sender = SysexSender(backend=self.send_backend)
        return app

    async def test_upload_list_download_roundtrip(self):
        from aiohttp import FormData
        form = FormData()
        form.add_field("file", self.SYX, filename="patch.syx",
                       content_type="application/octet-stream")
        resp = await self.client.post("/api/manage/sysex/upload", data=form)
        self.assertTrue((await resp.json())["ok"])

        resp = await self.client.get("/api/manage/sysex")
        body = await resp.json()
        self.assertEqual([e["name"] for e in body["library"]], ["patch.syx"])
        self.assertEqual(body["ports"], ["Synth A 20:0"])

        resp = await self.client.get("/api/manage/sysex/download",
                                     params={"name": "patch.syx"})
        self.assertEqual(await resp.read(), self.SYX)

    async def test_upload_rejects_invalid_sysex(self):
        from aiohttp import FormData
        form = FormData()
        form.add_field("file", b"\x00\x01", filename="bad.syx",
                       content_type="application/octet-stream")
        resp = await self.client.post("/api/manage/sysex/upload", data=form)
        self.assertEqual(resp.status, 400)

    async def test_execute_sends_and_remembers_defaults(self):
        self._deps.library.write("go.syx", self.SYX)
        resp = await self.client.post(
            "/api/manage/sysex/execute",
            json={"name": "go.syx", "port": "Synth A 20:0", "gap_ms": 0})
        body = await resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["messages"], 1)
        self.assertEqual(self.send_backend.sent, [self.SYX])
        defaults = json.load(open(self._deps.defaults_path))
        self.assertEqual(defaults["default_output"], "Synth A 20:0")

    async def test_execute_unknown_port_400(self):
        self._deps.library.write("go.syx", self.SYX)
        resp = await self.client.post(
            "/api/manage/sysex/execute",
            json={"name": "go.syx", "port": "Nope", "gap_ms": 0})
        self.assertEqual(resp.status, 400)

    async def test_upload_rejects_oversized_file(self):
        from aiohttp import FormData
        big = b"\xf0" + b"\x00" * (4 * 1024 * 1024 + 10) + b"\xf7"
        form = FormData()
        form.add_field("file", big, filename="huge.syx",
                       content_type="application/octet-stream")
        resp = await self.client.post("/api/manage/sysex/upload", data=form)
        self.assertEqual(resp.status, 413)

    async def test_upload_rejects_long_filename(self):
        from aiohttp import FormData
        long_name = ("x" * 300) + ".syx"
        form = FormData()
        form.add_field("file", self.SYX, filename=long_name,
                       content_type="application/octet-stream")
        resp = await self.client.post("/api/manage/sysex/upload", data=form)
        self.assertEqual(resp.status, 400)

    async def test_rename_rejects_long_new_name(self):
        self._deps.library.write("go.syx", self.SYX)
        long_name = ("x" * 300) + ".syx"
        resp = await self.client.post(
            "/api/manage/sysex/rename",
            json={"name": "go.syx", "new_name": long_name})
        self.assertEqual(resp.status, 400)

    async def test_execute_rejects_non_integer_gap_ms(self):
        self._deps.library.write("go.syx", self.SYX)
        for bad in (None, [1], {"a": 1}):
            resp = await self.client.post(
                "/api/manage/sysex/execute",
                json={"name": "go.syx", "port": "Synth A 20:0", "gap_ms": bad})
            self.assertEqual(resp.status, 400, f"gap_ms={bad!r}")
            body = await resp.json()
            self.assertFalse(body["ok"])

    async def test_upload_rejects_non_multipart_body(self):
        resp = await self.client.post("/api/manage/sysex/upload",
                                      json={"name": "not", "multipart": "form"})
        self.assertEqual(resp.status, 400)
        body = await resp.json()
        self.assertFalse(body["ok"])


if __name__ == "__main__":
    unittest.main()
