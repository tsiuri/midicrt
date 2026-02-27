#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"

python3 - <<'PY'
from pathlib import Path
import json

pilot_dir = Path("artifacts/pilot")
pilot_dir.mkdir(parents=True, exist_ok=True)

skeletons = {
    "merged_prs.json": [],
    "conflict_events.json": [],
    "ci_runs.json": [],
    "conflict_rate_summary.json": {
        "generated_at": None,
        "window_start": None,
        "window_end": None,
        "merged_pr_count": 0,
        "conflict_resolution_events": 0,
        "conflict_rate": 0.0,
        "source_files": {
            "merged_prs": "artifacts/pilot/merged_prs.json",
            "conflict_events": "artifacts/pilot/conflict_events.json"
        }
    },
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
            "mean_seconds": None
        },
        "by_workflow": {}
    }
}

for name, payload in skeletons.items():
    out = pilot_dir / name
    if out.exists():
        continue
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"initialized {out}")
PY

echo "Pilot artifact bootstrap complete at ${ROOT_DIR}/artifacts/pilot"
