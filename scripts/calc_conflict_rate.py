#!/usr/bin/env python3
"""Compute pilot conflict rate from merged PR and conflict-event exports."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _load_records(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("items"), list):
            return [x for x in data["items"] if isinstance(x, dict)]
        if isinstance(data.get("pull_requests"), list):
            return [x for x in data["pull_requests"] if isinstance(x, dict)]
        if isinstance(data.get("events"), list):
            return [x for x in data["events"] if isinstance(x, dict)]
    raise ValueError(f"Unsupported JSON shape in {path}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--merged-prs", required=True, help="Path to merged PR export JSON")
    ap.add_argument("--conflict-events", required=True, help="Path to conflict events JSON")
    ap.add_argument("--window-start", required=True, help="ISO8601 start timestamp")
    ap.add_argument("--window-end", required=True, help="ISO8601 end timestamp")
    ap.add_argument("--output", required=True, help="Output summary JSON path")
    ap.add_argument(
        "--generated-at",
        default=None,
        help="Optional fixed ISO8601 timestamp for deterministic outputs.",
    )
    args = ap.parse_args()

    merged = _load_records(Path(args.merged_prs))
    conflicts = _load_records(Path(args.conflict_events))

    merged_count = len(merged)
    conflict_count = len(conflicts)
    conflict_rate = (conflict_count / merged_count) if merged_count else 0.0

    generated_at = args.generated_at or datetime.now().astimezone().isoformat()

    summary = {
        "generated_at": generated_at,
        "window_start": args.window_start,
        "window_end": args.window_end,
        "merged_pr_count": merged_count,
        "conflict_resolution_events": conflict_count,
        "conflict_rate": round(conflict_rate, 6),
        "source_files": {
            "merged_prs": args.merged_prs,
            "conflict_events": args.conflict_events,
        },
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote conflict-rate summary: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
