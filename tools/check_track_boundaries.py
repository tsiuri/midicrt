#!/usr/bin/env python3
"""Enforce ownership boundaries between Track A and Track B.

Policy sources:
- docs/contributor_tracks.md
- docs/parallel_dev_playbook.md

The check fails when a change set touches both Track A and Track B paths unless an
explicit override exists through one of:
- ALLOW_CROSS_TRACK=1
- PR label "allow-cross-track" (mapped to env by CI)
- marker file .ci/allow_cross_track
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

CONTRIBUTOR_TRACKS = Path("docs/contributor_tracks.md")
PARALLEL_PLAYBOOK = Path("docs/parallel_dev_playbook.md")
OVERRIDE_FILE = Path(".ci/allow_cross_track")
OVERRIDE_LABEL = "allow-cross-track"


class PolicyError(RuntimeError):
    """Raised when policy docs cannot be parsed into a valid rule set."""


def _parse_track_paths(markdown: str, track_name: str) -> set[str]:
    # Expected heading format in docs/contributor_tracks.md:
    # - **Track A (...):** `path1`, `path2`
    pattern = rf"- \*\*{re.escape(track_name)}[^\n]*\n?"
    match = re.search(pattern, markdown)
    if not match:
        raise PolicyError(f"Missing '{track_name}' declaration in {CONTRIBUTOR_TRACKS}.")

    line = match.group(0)
    paths = set(re.findall(r"`([^`]+)`", line))
    if not paths:
        raise PolicyError(
            f"No file paths found in '{track_name}' declaration in {CONTRIBUTOR_TRACKS}."
        )
    return paths


def _load_policy() -> tuple[set[str], set[str]]:
    if not CONTRIBUTOR_TRACKS.exists():
        raise PolicyError(f"Missing policy file: {CONTRIBUTOR_TRACKS}")
    if not PARALLEL_PLAYBOOK.exists():
        raise PolicyError(f"Missing policy file: {PARALLEL_PLAYBOOK}")

    tracks_md = CONTRIBUTOR_TRACKS.read_text(encoding="utf-8")
    playbook_md = PARALLEL_PLAYBOOK.read_text(encoding="utf-8")

    track_a = _parse_track_paths(tracks_md, "Track A")
    track_b = _parse_track_paths(tracks_md, "Track B")

    if track_a & track_b:
        overlap = ", ".join(sorted(track_a & track_b))
        raise PolicyError(f"Track A/B overlap detected: {overlap}")

    # Ensure the playbook still documents escalation rules for cross-lane edits.
    if "contract-impact" not in playbook_md:
        raise PolicyError(
            f"{PARALLEL_PLAYBOOK} must document the contract-impact escalation workflow."
        )

    # Ensure contributor tracks doc still includes all documented override mechanisms.
    required_override_refs = ["ALLOW_CROSS_TRACK=1", OVERRIDE_LABEL, str(OVERRIDE_FILE)]
    missing_refs = [ref for ref in required_override_refs if ref not in tracks_md]
    if missing_refs:
        raise PolicyError(
            "Missing override references in docs/contributor_tracks.md: "
            + ", ".join(missing_refs)
        )

    return track_a, track_b


def _changed_files_from_git() -> tuple[set[str], bool]:
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


def _changed_files_from_file(path: Path) -> set[str]:
    if not path.exists():
        raise PolicyError(f"Changed-files fixture not found: {path}")
    contents = path.read_text(encoding="utf-8")
    return {line.strip() for line in contents.splitlines() if line.strip() and not line.startswith("#")}


def _override_enabled() -> bool:
    return os.environ.get("ALLOW_CROSS_TRACK") == "1" or OVERRIDE_FILE.exists()


def _print_track_changes(track_name: str, changed: list[str], to_stderr: bool = False) -> None:
    out = sys.stderr if to_stderr else sys.stdout
    print(f"{track_name} files changed:", file=out)
    for path in changed:
        print(f"  - {path}", file=out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--changed-files-file",
        type=Path,
        help="Optional newline-delimited file list for deterministic test fixtures.",
    )
    args = parser.parse_args()

    try:
        track_a, track_b = _load_policy()
    except PolicyError as exc:
        print(f"Policy parse error: {exc}", file=sys.stderr)
        return 2

    if args.changed_files_file:
        changed = _changed_files_from_file(args.changed_files_file)
    else:
        changed, ok = _changed_files_from_git()
        if not ok:
            return 2

    changed_a = sorted(changed & track_a)
    changed_b = sorted(changed & track_b)

    if not changed_a or not changed_b:
        print("Track boundary check passed.")
        return 0

    if _override_enabled():
        print("Track boundary check overridden via explicit override.")
        _print_track_changes("Track A", changed_a)
        _print_track_changes("Track B", changed_b)
        return 0

    print("Track boundary check failed: both Track A and Track B files changed.", file=sys.stderr)
    _print_track_changes("Track A", changed_a, to_stderr=True)
    _print_track_changes("Track B", changed_b, to_stderr=True)
    print(
        "Use PR label 'allow-cross-track' or add .ci/allow_cross_track (or set ALLOW_CROSS_TRACK=1) to override.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
