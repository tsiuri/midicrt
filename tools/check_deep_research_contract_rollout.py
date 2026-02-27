#!/usr/bin/env python3
"""Validate DeepResearch contract version declarations and rollout safeguards.

Policy enforced by this check:
- Contract version must be declared via MAJOR.MINOR integer constants.
- If MAJOR changes, compatibility tests must be updated in the same PR
  (reader-first rollout signal).
- If MINOR changes, compatibility tests must exist and pass in CI.
- Version must only move forward.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PLATFORM_PATH = "engine/deep_research/platform.py"
SCHEMA_PATH = "engine/state/schema.py"
COMPAT_TEST_PATH = "tests/test_deep_research_contract_compat.py"
COMPAT_FIXTURE_PATH = "tests/fixtures/deep_research_contract_cases.json"

_MAJOR_RE = re.compile(r"^RESEARCH_CONTRACT_MAJOR_VERSION\s*=\s*(\d+)\s*$", re.MULTILINE)
_MINOR_RE = re.compile(r"^RESEARCH_CONTRACT_MINOR_VERSION\s*=\s*(\d+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Version:
    major: int
    minor: int


def _read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _parse_version(src: str, label: str) -> Version:
    major_match = _MAJOR_RE.search(src)
    minor_match = _MINOR_RE.search(src)
    if not major_match or not minor_match:
        raise ValueError(f"{label} missing RESEARCH_CONTRACT_MAJOR_VERSION or RESEARCH_CONTRACT_MINOR_VERSION")
    return Version(major=int(major_match.group(1)), minor=int(minor_match.group(1)))


def _resolve_base_ref() -> str:
    for ref in ("origin/master", "origin/main", "master", "main", "HEAD~1"):
        probe = subprocess.run(["git", "rev-parse", "--verify", ref], capture_output=True, text=True, check=False)
        if probe.returncode == 0:
            return ref
    raise RuntimeError("unable to resolve base ref for contract rollout check")


def _git_show(base_ref: str, path: str) -> str:
    proc = subprocess.run(
        ["git", "show", f"{base_ref}:{path}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"unable to read {base_ref}:{path}")
    return proc.stdout


def _changed_files(base_ref: str) -> set[str]:
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "unable to list changed files")
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def _schema_breaking_change_detected(base_schema_src: str, head_schema_src: str) -> bool:
    # Conservative detector: schema-version bump or field removals/renames are treated as breaking.
    schema_version_re = re.compile(r"^SCHEMA_VERSION\s*=\s*(\d+)\s*$", re.MULTILINE)
    base_match = schema_version_re.search(base_schema_src)
    head_match = schema_version_re.search(head_schema_src)
    if not base_match or not head_match:
        raise ValueError("Unable to parse SCHEMA_VERSION in engine/state/schema.py")

    base_schema_version = int(base_match.group(1))
    head_schema_version = int(head_match.group(1))
    if head_schema_version != base_schema_version:
        return True

    # If schema changed but version did not, require explicit contract version bump as safeguard.
    return base_schema_src != head_schema_src


def main() -> int:
    base_ref = _resolve_base_ref()
    head_src = _read_text(PLATFORM_PATH)
    head_version = _parse_version(head_src, "HEAD")

    base_src = _git_show(base_ref, PLATFORM_PATH)
    base_version = _parse_version(base_src, base_ref)

    changed = _changed_files(base_ref)
    compat_test_touched = COMPAT_TEST_PATH in changed
    compat_fixture_touched = COMPAT_FIXTURE_PATH in changed

    head_schema_src = _read_text(SCHEMA_PATH)
    base_schema_src = _git_show(base_ref, SCHEMA_PATH)
    schema_breaking = _schema_breaking_change_detected(base_schema_src, head_schema_src)

    if head_version.major < base_version.major or (
        head_version.major == base_version.major and head_version.minor < base_version.minor
    ):
        print(
            "DeepResearch contract version moved backwards; version declarations must be monotonic.",
            file=sys.stderr,
        )
        print(f"origin/master={base_version.major}.{base_version.minor} HEAD={head_version.major}.{head_version.minor}", file=sys.stderr)
        return 1

    major_changed = head_version.major != base_version.major
    minor_changed = head_version.minor != base_version.minor

    if schema_breaking and not (major_changed or minor_changed):
        print(
            "Schema-breaking DeepResearch contract change detected without contract version bump.",
            file=sys.stderr,
        )
        print(
            "Action: bump RESEARCH_CONTRACT_MAJOR_VERSION or RESEARCH_CONTRACT_MINOR_VERSION "
            f"in {PLATFORM_PATH}.",
            file=sys.stderr,
        )
        return 1

    if schema_breaking and not major_changed:
        print(
            "Schema-breaking DeepResearch contract change requires a MAJOR version bump.",
            file=sys.stderr,
        )
        print(f"{base_ref}={base_version.major}.{base_version.minor} HEAD={head_version.major}.{head_version.minor}", file=sys.stderr)
        return 1

    if major_changed and not compat_test_touched:
        print(
            "Breaking DeepResearch contract major change detected, but compatibility tests were not updated.",
            file=sys.stderr,
        )
        print(f"Update {COMPAT_TEST_PATH} in the same PR to prove reader-first rollout.", file=sys.stderr)
        return 1

    if minor_changed and not Path(COMPAT_TEST_PATH).exists():
        print(
            f"Additive DeepResearch minor change detected, but required compatibility test file is missing: {COMPAT_TEST_PATH}",
            file=sys.stderr,
        )
        return 1

    if (major_changed or minor_changed or schema_breaking) and not compat_fixture_touched:
        print(
            "DeepResearch contract/schema change detected, but compatibility fixture was not updated.",
            file=sys.stderr,
        )
        print(
            f"Action: update {COMPAT_FIXTURE_PATH} in the same PR (or revert the contract/schema change).",
            file=sys.stderr,
        )
        return 1

    print(
        "DeepResearch contract rollout declaration check passed "
        f"({base_ref}={base_version.major}.{base_version.minor}, HEAD={head_version.major}.{head_version.minor})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
