#!/usr/bin/env python3
"""Dry-run evaluator for lane CODEOWNER approval gates.

Simulates the governance rule from docs/parallel_dev_playbook.md:
- Single-lane PR: one owning lane approval is sufficient.
- Cross-lane PR: all touched lane owners must approve.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

LANE_RULES = {
    "platform": (
        "engine/",
        "fb/",
        "ui/",
        ".github/workflows/",
    ),
    "logic": (
        "pages/",
        "plugins/",
        "engine/deep_research/logic.py",
        "tests/fixtures/",
    ),
    "qa-contract": (
        "tests/",
        ".ci/",
        "docs/contracts/",
    ),
    "observer": (
        "web/",
        "scripts/run_web_observer.py",
    ),
}


def lane_for_path(path: str) -> set[str]:
    normalized = path.lstrip("./")
    lanes: set[str] = set()
    for lane, rules in LANE_RULES.items():
        for rule in rules:
            if rule.endswith("/"):
                if normalized.startswith(rule):
                    lanes.add(lane)
            elif normalized == rule:
                lanes.add(lane)
    return lanes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--changed-files", nargs="+", required=True)
    parser.add_argument("--approved-lanes", nargs="*", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    touched: set[str] = set()
    for path in args.changed_files:
        touched |= lane_for_path(path)

    approved = set(args.approved_lanes)
    missing = sorted(touched - approved)
    passed = not missing and bool(touched)

    summary = {
        "changed_files": args.changed_files,
        "touched_lanes": sorted(touched),
        "approved_lanes": sorted(approved),
        "missing_lanes": missing,
        "result": "pass" if passed else "fail",
    }

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(summary)

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
