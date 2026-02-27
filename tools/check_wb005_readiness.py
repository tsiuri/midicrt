#!/usr/bin/env python3
"""Validate WB-005 pilot start readiness criteria.

Criteria:
1. WB-000..WB-004 are marked complete in docs/parallel_execution_board.md.
2. Required pilot docs are present.
3. Required pilot artifact files exist in artifacts/pilot/.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BOARD_PATH = Path("docs/parallel_execution_board.md")
REQUIRED_WORKSTREAMS = [f"WB-{n:03d}" for n in range(5)]
COMPLETE_TOKENS = ("complete", "completed", "closed", "done", "green")
REQUIRED_DOCS = [
    Path("docs/pilot_incident_log_template.md"),
    Path("docs/parallel_pilot_evidence_index.md"),
]
REQUIRED_ARTIFACTS = [
    Path("artifacts/pilot/merged_prs.json"),
    Path("artifacts/pilot/conflict_events.json"),
    Path("artifacts/pilot/conflict_rate_summary.json"),
    Path("artifacts/pilot/ci_runs.json"),
    Path("artifacts/pilot/ci_timing_summary.json"),
]


def _ok(message: str) -> bool:
    print(f"PASS: {message}")
    return True


def _fail(message: str) -> bool:
    print(f"FAIL: {message}")
    return False


def _normalize_status(status: str) -> str:
    return status.lower().strip()


def _is_complete_status(status: str) -> bool:
    normalized = _normalize_status(status)
    return any(token in normalized for token in COMPLETE_TOKENS)


def _extract_workstream_statuses(board_text: str) -> dict[str, str]:
    statuses: dict[str, str] = {}
    wb_pattern = re.compile(r"\b(WB-\d{3})\b")
    for raw_line in board_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue

        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 4:
            continue

        match = wb_pattern.search(cells[0])
        if not match:
            continue

        stream = match.group(1)
        if stream in REQUIRED_WORKSTREAMS:
            statuses[stream] = cells[3]

    return statuses


def check_workstream_completion(board_path: Path) -> bool:
    if not board_path.exists():
        return _fail(f"missing board file: {board_path}")

    board_text = board_path.read_text(encoding="utf-8")
    statuses = _extract_workstream_statuses(board_text)

    all_ok = True
    for stream in REQUIRED_WORKSTREAMS:
        status = statuses.get(stream)
        if status is None:
            all_ok = _fail(f"{stream} status row not found in {board_path}") and all_ok
            continue

        if _is_complete_status(status):
            all_ok = _ok(f"{stream} marked complete (status: {status})") and all_ok
        else:
            all_ok = _fail(f"{stream} not complete (status: {status})") and all_ok

    return all_ok


def check_paths_exist(label: str, paths: list[Path]) -> bool:
    all_ok = True
    for path in paths:
        if path.exists():
            all_ok = _ok(f"{label} present: {path}") and all_ok
        else:
            all_ok = _fail(f"{label} missing: {path}") and all_ok
    return all_ok


def main() -> int:
    print("WB-005 readiness check")
    print("=" * 24)

    checks = [
        check_workstream_completion(BOARD_PATH),
        check_paths_exist("required doc", REQUIRED_DOCS),
        check_paths_exist("required artifact", REQUIRED_ARTIFACTS),
    ]

    if all(checks):
        print("\nRESULT: READY - all WB-005 readiness criteria passed.")
        return 0

    print("\nRESULT: NOT READY - one or more WB-005 readiness criteria failed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
