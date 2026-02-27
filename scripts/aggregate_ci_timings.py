#!/usr/bin/env python3
"""Aggregate GitHub Actions run timings into machine-readable JSON.

Input:
  JSON from `gh run list --json ...` or `gh api` workflow-runs payload.

Examples:
  gh run list --limit 200 --json databaseId,workflowName,status,conclusion,createdAt,updatedAt \
    > artifacts/ci_runs.json
  python scripts/aggregate_ci_timings.py --input artifacts/ci_runs.json --output artifacts/ci_timing_summary.json
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


def _parse_iso8601(ts: str) -> datetime:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def _duration_seconds(run: dict[str, Any]) -> float | None:
    start = run.get("run_started_at") or run.get("createdAt") or run.get("created_at")
    end = run.get("updated_at") or run.get("updatedAt")
    if not start or not end:
        return None
    try:
        return (_parse_iso8601(end) - _parse_iso8601(start)).total_seconds()
    except ValueError:
        return None


def _normalize_runs(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        if isinstance(payload.get("workflow_runs"), list):
            return [r for r in payload["workflow_runs"] if isinstance(r, dict)]
        if isinstance(payload.get("runs"), list):
            return [r for r in payload["runs"] if isinstance(r, dict)]
    raise ValueError("Unsupported input JSON shape; expected list, {workflow_runs:[...]}, or {runs:[...]}")


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        raise ValueError("Cannot compute percentile for empty sequence")
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * percentile
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return sorted_values[low]
    frac = rank - low
    return sorted_values[low] * (1.0 - frac) + sorted_values[high] * frac


def _build_stats(samples: Iterable[float]) -> dict[str, Any]:
    values = sorted(samples)
    if not values:
        return {
            "sample_count": 0,
            "median_seconds": None,
            "p95_seconds": None,
            "min_seconds": None,
            "max_seconds": None,
            "mean_seconds": None,
        }
    return {
        "sample_count": len(values),
        "median_seconds": round(statistics.median(values), 3),
        "p95_seconds": round(_percentile(values, 0.95), 3),
        "min_seconds": round(values[0], 3),
        "max_seconds": round(values[-1], 3),
        "mean_seconds": round(statistics.fmean(values), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Path to workflow run JSON input")
    parser.add_argument("--output", required=True, help="Path to output summary JSON artifact")
    parser.add_argument(
        "--conclusion",
        default="success",
        help="Filter by run conclusion (default: success). Use 'any' to include all conclusions.",
    )
    parser.add_argument(
        "--generated-at",
        default=None,
        help="Optional fixed ISO8601 timestamp for deterministic outputs.",
    )
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    runs = _normalize_runs(payload)

    grouped: dict[str, list[float]] = defaultdict(list)
    skipped = 0
    for run in runs:
        if args.conclusion != "any":
            if run.get("conclusion") != args.conclusion:
                continue
        dur = _duration_seconds(run)
        if dur is None or dur < 0:
            skipped += 1
            continue
        workflow_name = run.get("workflowName") or run.get("name") or run.get("workflow_name") or "unknown"
        grouped[str(workflow_name)].append(dur)

    all_samples = [v for values in grouped.values() for v in values]
    by_workflow = {name: _build_stats(samples) for name, samples in sorted(grouped.items())}

    generated_at = args.generated_at or datetime.now().astimezone().isoformat()

    output = {
        "generated_at": generated_at,
        "input_path": args.input,
        "filters": {"conclusion": args.conclusion},
        "run_count_in_payload": len(runs),
        "run_count_used": len(all_samples),
        "run_count_skipped": skipped,
        "overall": _build_stats(all_samples),
        "by_workflow": by_workflow,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote CI timing summary: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
