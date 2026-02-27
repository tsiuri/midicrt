import json
import sys
import types
import unittest

if "blessed" not in sys.modules:
    blessed = types.ModuleType("blessed")

    class _Terminal:
        number_of_colors = 0

        def move_yx(self, *_args, **_kwargs):
            return ""

        clear_eol = ""
        normal = ""

        @staticmethod
        def reverse(text):
            return text

        @staticmethod
        def strip_seqs(text):
            return text

        @staticmethod
        def bold(text):
            return text

        @staticmethod
        def color(_n):
            return lambda text: text

    blessed.Terminal = _Terminal
    sys.modules["blessed"] = blessed

if "configutil" not in sys.modules:
    configutil = types.ModuleType("configutil")
    configutil.load_section = lambda _name: {}
    configutil.save_section = lambda _name, _data: None
    sys.modules["configutil"] = configutil


try:
    import aiohttp  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover - local fallback for offline env
    aiohttp = types.ModuleType("aiohttp")
    aiohttp.WSMsgType = types.SimpleNamespace(TEXT="TEXT", ERROR="ERROR")
    aiohttp.web = types.SimpleNamespace(
        Application=dict,
        WebSocketResponse=object,
        Request=object,
        StreamResponse=object,
        Response=object,
        FileResponse=lambda *_a, **_k: None,
        json_response=lambda payload: payload,
        run_app=lambda *_a, **_k: None,
    )
    sys.modules["aiohttp"] = aiohttp

from ui.model import (
    CaptureStatusWidget,
    MicrotimingHistogramWidget,
    ModuleHealthCard,
    ModuleHealthWidget,
    TempoQualityWidget,
)
from ui.renderers.pixel import PixelRenderer
from ui.renderers.text import TextRenderer
from web.observer import DashboardServer


class ObserverWidgetParityTest(unittest.TestCase):
    @staticmethod
    def _lines(renderer, widget):
        return ["".join(seg.text for seg in line.segments) for line in renderer._flatten(widget)]

    def test_tempo_microtiming_capture_module_health_flatten_parity(self):
        text = TextRenderer()
        pixel = PixelRenderer()

        widgets = [
            TempoQualityWidget(bpm=123.45, confidence=0.91, stability=0.74, lock_state="locked", meter="4/4"),
            MicrotimingHistogramWidget(
                title="Microtiming",
                buckets=[("<-20ms", 2), ("-10ms", 4), ("0ms", 8), ("+10ms", 4), (">+20ms", 1)],
                total_samples=19,
            ),
            CaptureStatusWidget(
                armed=True,
                state="recording",
                target_path="/tmp/capture.mid",
                last_commit="abc1234",
                last_commit_age_s=1.2,
            ),
            ModuleHealthWidget(
                cards=[
                    ModuleHealthCard(name="tempo", status="ok", latency_ms=1.0, drop_rate=0.0, detail="stable"),
                    ModuleHealthCard(name="observer", status="warn", latency_ms=12.0, drop_rate=0.04, detail="queue pressure"),
                ]
            ),
        ]

        for widget in widgets:
            with self.subTest(widget=type(widget).__name__):
                self.assertEqual(self._lines(text, widget), self._lines(pixel, widget))

    def test_observer_payload_includes_widget_bridge_sections(self):
        server = DashboardServer(socket_path="/tmp/test.sock", host="127.0.0.1", port=0)
        payload = server._payload_for(
            2,
            {
                "schema_version": 4,
                "transport": {"tick": 22, "bpm": 132.0, "confidence": 0.88, "meter_estimate": "7/8"},
                "views": {
                    "tempo_quality": {"stability": 0.81, "lock_state": "locked"},
                    "microtiming": {"buckets": [["-5ms", 3], ["0ms", 9]], "total_samples": 12},
                    "capture_status": {"armed": True, "state": "idle", "last_commit": "deadbee"},
                    "module_health": {"cards": [{"name": "clock", "status": "ok", "latency_ms": 0.8, "drop_rate": 0.0}]},
                },
            },
            {"connected": True, "last_update_age_ms": 12.0},
            last_seq=1,
            telemetry={"queue_dropped": 0, "queue_coalesced": 0},
        )
        data = json.loads(payload)
        bridge = data["observer_views"]
        self.assertEqual(bridge["tempo_quality"]["meter"], "7/8")
        self.assertEqual(bridge["capture_status"]["last_commit"], "deadbee")
        self.assertEqual(bridge["module_health"]["cards"][0]["name"], "clock")


if __name__ == "__main__":
    unittest.main()
