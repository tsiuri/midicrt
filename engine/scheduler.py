from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class ModulePolicy:
    enabled: bool = True
    policy: str = "event_driven"
    interval_hz: float = 10.0


class ModuleScheduler:
    """Policy-driven scheduler + timing instrumentation for engine modules."""

    def __init__(self, module_policies: dict[str, dict[str, Any]] | None = None, overload_cost_ms: float = 6.0) -> None:
        self._cfg = module_policies if isinstance(module_policies, dict) else {}
        self._state: dict[str, dict[str, Any]] = {}
        self._overload_cost_ms = max(0.1, float(overload_cost_ms))

    def _policy_for(self, name: str) -> ModulePolicy:
        raw = self._cfg.get(name, {}) if isinstance(self._cfg.get(name, {}), dict) else {}
        enabled = bool(raw.get("enabled", True))
        policy = str(raw.get("policy", "event_driven")).strip().lower()
        if policy not in {"event_driven", "clock_driven", "interval_hz"}:
            policy = "event_driven"
        interval_hz = float(raw.get("interval_hz", 10.0))
        return ModulePolicy(enabled=enabled, policy=policy, interval_hz=max(0.1, interval_hz))

    def should_run(self, name: str, event_kind: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        pol = self._policy_for(name)
        slot = self._state.setdefault(name, {
            "last_run_ts": 0.0,
            "avg_cost_ms": 0.0,
            "max_cost_ms": 0.0,
            "last_start_ts": 0.0,
            "last_end_ts": 0.0,
            "runs": 0,
            "skips": 0,
            "overload": False,
        })
        if not pol.enabled:
            slot["skips"] += 1
            return False
        if pol.policy == "clock_driven":
            ok = event_kind == "clock"
            if not ok:
                slot["skips"] += 1
            return ok
        if pol.policy == "event_driven":
            ok = event_kind != "clock"
            if not ok:
                slot["skips"] += 1
            return ok

        # interval_hz
        min_dt = 1.0 / pol.interval_hz
        if (now - float(slot.get("last_run_ts", 0.0))) >= min_dt:
            return True
        slot["skips"] += 1
        return False

    def begin(self, name: str, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        slot = self._state.setdefault(name, {})
        slot["last_start_ts"] = now
        return now

    def end(self, name: str, start_ts: float, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        cost_ms = max(0.0, (now - start_ts) * 1000.0)
        slot = self._state.setdefault(name, {})
        slot["last_end_ts"] = now
        slot["last_run_ts"] = now
        slot["runs"] = int(slot.get("runs", 0)) + 1
        prev_avg = float(slot.get("avg_cost_ms", 0.0))
        alpha = 0.2
        slot["avg_cost_ms"] = cost_ms if prev_avg <= 0.0 else ((1.0 - alpha) * prev_avg + alpha * cost_ms)
        slot["max_cost_ms"] = max(float(slot.get("max_cost_ms", 0.0)), cost_ms)
        slot["overload"] = bool(slot.get("avg_cost_ms", 0.0) >= self._overload_cost_ms)
        return cost_ms

    def module_diag(self, name: str) -> dict[str, Any]:
        slot = self._state.get(name, {})
        pol = self._policy_for(name)
        return {
            "enabled": pol.enabled,
            "policy": pol.policy,
            "interval_hz": pol.interval_hz,
            "last_start_ts": float(slot.get("last_start_ts", 0.0)),
            "last_end_ts": float(slot.get("last_end_ts", 0.0)),
            "avg_cost_ms": round(float(slot.get("avg_cost_ms", 0.0)), 3),
            "max_cost_ms": round(float(slot.get("max_cost_ms", 0.0)), 3),
            "runs": int(slot.get("runs", 0)),
            "skips": int(slot.get("skips", 0)),
            "overload": bool(slot.get("overload", False)),
        }

    def diagnostics(self, module_names: list[str]) -> dict[str, Any]:
        mods = {name: self.module_diag(name) for name in module_names}
        overloaded = [name for name, info in mods.items() if info.get("overload")]
        return {
            "scheduler": {
                "overload_cost_ms": self._overload_cost_ms,
                "overloaded_modules": overloaded,
                "module_count": len(mods),
            },
            "modules": mods,
        }
