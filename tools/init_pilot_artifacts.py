#!/usr/bin/env python3
"""Initialize required WB-005 pilot artifact JSON files."""

from __future__ import annotations

import json
from pathlib import Path

PILOT_DIR = Path("artifacts/pilot")
SKELETONS = {
    "merged_prs.json": [],
    "conflict_events.json": [],
    "conflict_rate_summary.json": {
        "generated_at": None,
        "window_start": None,
        "window_end": None,
        "merged_pr_count": 0,
        "conflict_resolution_events": 0,
        "conflict_rate": 0.0,
        "source_files": {
            "merged_prs": "artifacts/pilot/merged_prs.json",
            "conflict_events": "artifacts/pilot/conflict_events.json",
        },
    },
    "ci_runs.json": [],
    "ci_timing_summary.json": {
        "generated_at": None,
        "input_path": "artifacts/pilot/ci_runs.json",
        "filters": {"conclusion": "success"},
        "run_count_in_payload": 0,
        "run_count_used": 0,
        "run_count_skipped": 0,
        "overall": {
            "sample_count": 0,
            "median_seconds": None,
            "p95_seconds": None,
            "min_seconds": None,
            "max_seconds": None,
            "mean_seconds": None,
        },
        "by_workflow": {},
    },
}


def main() -> int:
    PILOT_DIR.mkdir(parents=True, exist_ok=True)

    for name, payload in SKELETONS.items():
        out = PILOT_DIR / name
        if out.exists():
            print(f"exists: {out}")
            continue
        out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"created: {out}")

    print(f"pilot artifacts initialized at {PILOT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
