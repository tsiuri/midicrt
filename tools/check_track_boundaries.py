#!/usr/bin/env python3
"""Enforce ownership boundaries and PR metadata policy.

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
LANE_ORDER = ("platform", "logic", "qa-contract", "observer")
BRANCH_NAME_RE = re.compile(
    r"^agent/(?P<lane>platform|logic|qa-contract|observer)/"
    r"(?P<ticket>[A-Za-z0-9][A-Za-z0-9-]*)-(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)$"
)


class PolicyError(RuntimeError):
    """Raised when policy docs cannot be parsed into a valid rule set."""


def _parse_lane_roots(markdown: str) -> dict[str, set[str]]:
    """Parse lane -> directory root ownership from playbook markdown."""
    lane_roots: dict[str, set[str]] = {lane: set() for lane in LANE_ORDER}

    for lane in LANE_ORDER:
        section_pattern = re.compile(
            rf"### `{re.escape(lane)}`(?P<body>.*?)(?:\n### `|\Z)",
            re.DOTALL,
        )
        section_match = section_pattern.search(markdown)
        if not section_match:
            raise PolicyError(f"Missing lane section '{lane}' in {PARALLEL_PLAYBOOK}.")

        body = section_match.group("body")
        typical_line = re.search(r"Typical directories:\s*(.+)", body)
        if not typical_line:
            raise PolicyError(
                f"Missing 'Typical directories' list for lane '{lane}' in {PARALLEL_PLAYBOOK}."
            )

        roots = {path.strip() for path in re.findall(r"`([^`]+)`", typical_line.group(1))}
        if not roots:
            raise PolicyError(
                f"No directory roots found for lane '{lane}' in {PARALLEL_PLAYBOOK}."
            )
        lane_roots[lane] = roots

    return lane_roots


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


def _load_policy() -> tuple[set[str], set[str], dict[str, set[str]]]:
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

    lane_roots = _parse_lane_roots(playbook_md)

    return track_a, track_b, lane_roots


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


def _parse_pr_lane(pr_body: str) -> str | None:
    for line in pr_body.splitlines():
        match = re.match(r"\s*-\s*Lane:\s*(.+?)\s*$", line)
        if not match:
            continue
        value = match.group(1).strip()
        value = re.sub(r"<!--.*?-->", "", value).strip()
        if value in LANE_ORDER:
            return value
    return None


def _has_contract_impact_field(pr_body: str) -> bool:
    return "contract-impact" in pr_body


def _contract_impact_checked(pr_body: str) -> bool:
    for line in pr_body.splitlines():
        if "contract-impact" not in line:
            continue
        normalized = line.lower().replace(" ", "")
        if "-[x]" in normalized or "-[X]" in line:
            return True
    return False


def _lane_for_file(path: str, lane_roots: dict[str, set[str]]) -> set[str]:
    owners: set[str] = set()
    normalized = path.lstrip("./")
    for lane, roots in lane_roots.items():
        for root in roots:
            root_prefix = root.rstrip("/") + "/"
            if normalized.startswith(root_prefix):
                owners.add(lane)
    return owners


def _validate_pr_metadata(
    branch_name: str,
    pr_body: str,
    changed: set[str],
    lane_roots: dict[str, set[str]],
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    branch_match = BRANCH_NAME_RE.match(branch_name)
    if not branch_match:
        errors.append(
            "Branch name must match agent/<lane>/<ticket>-<slug> with lane in "
            "{platform, logic, qa-contract, observer}."
        )
        branch_lane = None
    else:
        branch_lane = branch_match.group("lane")

    if not _has_contract_impact_field(pr_body):
        errors.append("PR template must include the contract-impact declaration field.")

    pr_lane = _parse_pr_lane(pr_body)
    if not pr_lane:
        errors.append("PR template must set '- Lane:' to one of platform|logic|qa-contract|observer.")

    declared_lane = pr_lane or branch_lane
    if pr_lane and branch_lane and pr_lane != branch_lane:
        errors.append(f"Lane mismatch: branch lane '{branch_lane}' does not match PR lane '{pr_lane}'.")

    if declared_lane:
        cross_lane_files: list[str] = []
        for path in sorted(changed):
            owners = _lane_for_file(path, lane_roots)
            if owners and declared_lane not in owners:
                cross_lane_files.append(path)

        if cross_lane_files and not _contract_impact_checked(pr_body):
            sample = ", ".join(cross_lane_files[:5])
            errors.append(
                "Cross-lane edits detected without contract-impact marker. "
                f"Examples: {sample}"
            )

    return (not errors), errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--changed-files-file",
        type=Path,
        help="Optional newline-delimited file list for deterministic test fixtures.",
    )
    parser.add_argument("--validate-pr-metadata", action="store_true")
    parser.add_argument("--branch-name", help="Branch name to validate (or BRANCH_NAME env).")
    parser.add_argument("--pr-body-file", type=Path, help="Path to PR body markdown for validation.")
    args = parser.parse_args()

    try:
        track_a, track_b, lane_roots = _load_policy()
    except PolicyError as exc:
        print(f"Policy parse error: {exc}", file=sys.stderr)
        return 2

    if args.changed_files_file:
        changed = _changed_files_from_file(args.changed_files_file)
    else:
        changed, ok = _changed_files_from_git()
        if not ok:
            return 2

    if args.validate_pr_metadata:
        branch_name = (args.branch_name or os.environ.get("BRANCH_NAME", "")).strip()
        if not branch_name:
            print("PR metadata validation error: missing branch name.", file=sys.stderr)
            return 2

        pr_body = ""
        if args.pr_body_file:
            if not args.pr_body_file.exists():
                print(f"PR metadata validation error: missing PR body file {args.pr_body_file}", file=sys.stderr)
                return 2
            pr_body = args.pr_body_file.read_text(encoding="utf-8")
        else:
            pr_body = os.environ.get("PR_BODY", "")

        ok, errors = _validate_pr_metadata(branch_name, pr_body, changed, lane_roots)
        if ok:
            print("PR metadata guard passed.")
            return 0

        print("PR metadata guard failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

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
