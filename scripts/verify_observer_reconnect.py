#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
import time
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest import mock

import types

try:
    import aiohttp  # type: ignore # noqa: F401
except ModuleNotFoundError:
    aiohttp = types.ModuleType("aiohttp")
    aiohttp.WSMsgType = types.SimpleNamespace(TEXT="TEXT", ERROR="ERROR")
    aiohttp.web = types.SimpleNamespace(
        Application=object,
        WebSocketResponse=object,
        Request=object,
        StreamResponse=object,
        Response=object,
        FileResponse=lambda *_a, **_k: None,
        json_response=lambda payload: payload,
        run_app=lambda *_a, **_k: None,
    )
    sys.modules["aiohttp"] = aiohttp

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from web.observer import SnapshotBridge


@dataclass
class Step:
    connect_error: Exception | None
    snapshots: list[dict[str, Any] | Exception | None]


class PlannedSnapshotClient:
    plans: list[Step] = []

    def __init__(self, *args, **kwargs):
        if not PlannedSnapshotClient.plans:
            raise RuntimeError("no remaining client plans")
        self.plan = PlannedSnapshotClient.plans.pop(0)

    def connect(self) -> None:
        if self.plan.connect_error is not None:
            raise self.plan.connect_error

    def recv_snapshot(self) -> dict[str, Any] | None:
        if not self.plan.snapshots:
            return None
        item = self.plan.snapshots.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        return None


def _wait_until(predicate, timeout_s: float = 1.5, poll_s: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll_s)
    return False


def run_verification(reconnect_delay_s: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="midicrt-observer-") as tmp:
        socket_path = str(Path(tmp) / "observer.sock")
        PlannedSnapshotClient.plans = [
            Step(connect_error=OSError("socket missing"), snapshots=[]),
            Step(connect_error=None, snapshots=[{"schema_version": 4, "transport": {"tick": 101}}, OSError("socket dropped")]),
            Step(connect_error=None, snapshots=[{"schema_version": 4, "transport": {"tick": 102}}, None]),
        ]

        bridge = SnapshotBridge(socket_path=socket_path, reconnect_delay_s=reconnect_delay_s)
        with mock.patch("web.observer.SnapshotClient", PlannedSnapshotClient):
            bridge.start()

            ok_tick_101 = _wait_until(lambda: bridge.current()[0] >= 1)
            seq1, snap1, meta1 = bridge.current()

            ok_reconnect = _wait_until(lambda: bridge.current()[2]["reconnect_attempts"] >= 2)
            ok_tick_102 = _wait_until(lambda: (bridge.current()[1] or {}).get("transport", {}).get("tick") == 102)
            seq2, snap2, meta2 = bridge.current()
            bridge.stop()

    assert ok_tick_101, "bridge never published first post-reconnect snapshot"
    assert ok_reconnect, "reconnect attempts did not increase after disconnect"
    assert ok_tick_102, "bridge never recovered to second socket incarnation"
    assert snap1 and snap1.get("transport", {}).get("tick") == 101
    assert snap2 and snap2.get("transport", {}).get("tick") == 102
    assert meta1["reconnect_attempts"] >= 1
    assert meta2["reconnect_attempts"] >= 2
    assert isinstance(meta2.get("last_update_age_ms"), (int, float))

    return {
        "ok": True,
        "reconnect_delay_s": reconnect_delay_s,
        "seq_before": seq1,
        "seq_after": seq2,
        "meta_before": meta1,
        "meta_after": meta2,
        "checks": {
            "socket_disappearance_detected": meta1["reconnect_attempts"] >= 1,
            "socket_reappearance_recovered": snap2["transport"]["tick"] == 102,
            "health_counter_progression": meta2["reconnect_attempts"] >= meta1["reconnect_attempts"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic reconnect verification for SnapshotBridge")
    parser.add_argument("--reconnect-delay-s", type=float, default=0.02, help="Bridge reconnect delay used for deterministic checks")
    args = parser.parse_args()

    result = run_verification(reconnect_delay_s=max(0.01, args.reconnect_delay_s))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
