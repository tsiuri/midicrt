#!/usr/bin/env python3
"""Enforce Deep Research ownership boundaries between Track A and Track B.

This script inspects files changed in `origin/master...HEAD` and fails if both
Track A and Track B files are modified in the same PR, unless an explicit
override is provided.

Override options:
- environment variable ALLOW_CROSS_TRACK=1
- repository marker file .ci/allow_cross_track
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TRACK_A = {
    "engine/deep_research/platform.py",
    "engine/deep_research/mock_module.py",
}

TRACK_B = {
    "engine/deep_research/logic.py",
    "tests/fixtures/deep_research_sequences.json",
    "tests/test_deep_research_tracks.py",
}

OVERRIDE_FILE = Path('.ci/allow_cross_track')


def _changed_files() -> tuple[set[str], bool]:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "origin/master...HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        print("Failed to compute changed files from git diff.", file=sys.stderr)
        if exc.stderr:
            print(exc.stderr.strip(), file=sys.stderr)
        return set(), False

    return {line.strip() for line in result.stdout.splitlines() if line.strip()}, True


def _override_enabled() -> bool:
    return os.environ.get("ALLOW_CROSS_TRACK") == "1" or OVERRIDE_FILE.exists()


def main() -> int:
    changed, ok = _changed_files()
    if not ok:
        return 2
    changed_a = sorted(changed & TRACK_A)
    changed_b = sorted(changed & TRACK_B)

    if not changed_a or not changed_b:
        print("Track boundary check passed.")
        return 0

    if _override_enabled():
        print("Track boundary check overridden via explicit override.")
        print("Track A files changed:")
        for path in changed_a:
            print(f"  - {path}")
        print("Track B files changed:")
        for path in changed_b:
            print(f"  - {path}")
        return 0

    print("Track boundary check failed: both Track A and Track B files changed.", file=sys.stderr)
    print("Track A files changed:", file=sys.stderr)
    for path in changed_a:
        print(f"  - {path}", file=sys.stderr)
    print("Track B files changed:", file=sys.stderr)
    for path in changed_b:
        print(f"  - {path}", file=sys.stderr)
    print(
        "Use ALLOW_CROSS_TRACK=1 (contract-only changes) or add .ci/allow_cross_track to override.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
