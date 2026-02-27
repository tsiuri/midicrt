#!/usr/bin/env python3
"""Run daily pilot aggregation and update stable summary artifacts.

This orchestrator consumes raw exports in ``artifacts/pilot`` and rewrites:
- artifacts/pilot/conflict_rate_summary.json
- artifacts/pilot/ci_timing_summary.json
"""

from __future__ import annotations

import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PILOT_DIR = ROOT / "artifacts" / "pilot"


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pilot-dir", default=str(PILOT_DIR), help="Pilot artifacts directory")
    ap.add_argument("--window-start", required=True, help="ISO8601 start timestamp for pilot window")
    ap.add_argument("--window-end", required=True, help="ISO8601 end timestamp for pilot window")
    ap.add_argument(
        "--ci-conclusion",
        default="success",
        help="Workflow run conclusion filter for CI summary (default: success)",
    )
    ap.add_argument(
        "--generated-at",
        default=None,
        help="Optional fixed timestamp for deterministic summary rewrites",
    )
    args = ap.parse_args()

    pilot_dir = Path(args.pilot_dir)
    pilot_dir.mkdir(parents=True, exist_ok=True)

    generated_at = args.generated_at or _iso_utc_now()

    merged_prs = pilot_dir / "merged_prs.json"
    conflict_events = pilot_dir / "conflict_events.json"
    conflict_summary = pilot_dir / "conflict_rate_summary.json"
    ci_runs = pilot_dir / "ci_runs.json"
    ci_summary = pilot_dir / "ci_timing_summary.json"

    _run(
        [
            "python3",
            str(ROOT / "scripts" / "calc_conflict_rate.py"),
            "--merged-prs",
            str(merged_prs),
            "--conflict-events",
            str(conflict_events),
            "--window-start",
            args.window_start,
            "--window-end",
            args.window_end,
            "--generated-at",
            generated_at,
            "--output",
            str(conflict_summary),
        ]
    )

    _run(
        [
            "python3",
            str(ROOT / "scripts" / "aggregate_ci_timings.py"),
            "--input",
            str(ci_runs),
            "--output",
            str(ci_summary),
            "--conclusion",
            args.ci_conclusion,
            "--generated-at",
            generated_at,
        ]
    )

    print(f"Daily pilot aggregation complete in {pilot_dir}")
    print(f"  conflict summary: {conflict_summary}")
    print(f"  ci timing summary: {ci_summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
