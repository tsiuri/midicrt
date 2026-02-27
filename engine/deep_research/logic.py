from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from harmony import CHORDS, NOTE_NAMES

from .platform import ResearchContract, contract_versions_compatible, current_contract_version


@dataclass(frozen=True)
class ResearchResult:
    motif_span: int
    note_density: str
    active_note_total: int
    chord_candidates: list[dict[str, Any]]
    key_estimate: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "motif_span": self.motif_span,
            "note_density": self.note_density,
            "active_note_total": self.active_note_total,
            "chord_candidates": self.chord_candidates,
            "key_estimate": self.key_estimate,
        }


TOP_CHORD_CANDIDATES = 3
KEY_CONFIDENCE_THRESHOLD = 0.72
KEY_ALT_MARGIN = 0.08

MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)
ROMAN_DEGREES = {
    0: "I",
    1: "bII",
    2: "II",
    3: "bIII",
    4: "III",
    5: "IV",
    6: "#IV",
    7: "V",
    8: "bVI",
    9: "VI",
    10: "bVII",
    11: "VII",
}
HARMONIC_FUNCTIONS = {
    0: "tonic",
    2: "predominant",
    4: "mediant",
    5: "subdominant",
    7: "dominant",
    9: "submediant",
    11: "leading",
}


def _round4(value: float) -> float:
    return round(value + 1e-12, 4)


def _build_chord_candidates(flattened: list[int]) -> list[dict[str, Any]]:
    pcs = sorted({note % 12 for note in flattened})
    if len(pcs) < 2:
        return []

    candidates: list[dict[str, Any]] = []
    pcs_set = set(pcs)
    for chord in CHORDS:
        for root in range(12):
            if root not in pcs_set:
                continue
            pattern = {(root + interval) % 12 for interval in chord["pcs"]}
            match = len(pattern & pcs_set)
            if match < 2:
                continue
            coverage = match / len(pattern)
            precision = match / len(pcs_set)
            confidence = 0.65 * coverage + 0.35 * precision
            missing = sorted(pattern - pcs_set)
            extra = len(pcs_set - pattern)
            candidates.append(
                {
                    "label": f"{NOTE_NAMES[root]} {chord['name']}",
                    "root": root,
                    "name": chord["name"],
                    "confidence": _round4(confidence),
                    "missing_tones": [NOTE_NAMES[note] for note in missing],
                    "_sort_extra": extra,
                    "_sort_missing": len(missing),
                }
            )

    candidates.sort(
        key=lambda item: (
            -item["confidence"],
            item["_sort_extra"],
            item["_sort_missing"],
            item["root"],
            item["name"],
        )
    )
    deduped: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    for item in candidates:
        if item["label"] in seen_labels:
            continue
        seen_labels.add(item["label"])
        item.pop("_sort_extra", None)
        item.pop("_sort_missing", None)
        deduped.append(item)
        if len(deduped) >= TOP_CHORD_CANDIDATES:
            break
    return deduped


def _compute_key_estimate(flattened: list[int]) -> dict[str, Any] | None:
    pcs = sorted({note % 12 for note in flattened})
    if not pcs:
        return None
    pcs_set = set(pcs)
    scores: list[dict[str, Any]] = []
    for root in range(12):
        for mode, intervals in (("maj", MAJOR_SCALE), ("min", MINOR_SCALE)):
            scale = {(root + interval) % 12 for interval in intervals}
            inside = len(pcs_set & scale)
            outside = len(pcs_set - scale)
            score = (inside / len(pcs_set)) - (outside / len(pcs_set)) * 0.5
            scores.append(
                {
                    "label": f"{NOTE_NAMES[root]} {mode}",
                    "root": root,
                    "mode": mode,
                    "confidence": _round4(inside / len(pcs_set)),
                    "score": score,
                }
            )
    scores.sort(key=lambda item: (-item["score"], item["root"], item["mode"]))
    best = scores[0]
    alternatives = [
        {"label": alt["label"], "confidence": alt["confidence"]}
        for alt in scores[1:]
        if (best["score"] - alt["score"]) <= KEY_ALT_MARGIN
    ][:3]
    return {
        "label": best["label"],
        "confidence": best["confidence"],
        "alternatives": alternatives,
        "root": best["root"],
        "mode": best["mode"],
    }


def _roman_numeral(chord_root: int, chord_name: str, key_root: int) -> str:
    interval = (chord_root - key_root) % 12
    roman = ROMAN_DEGREES.get(interval, "?")
    if chord_name in {"m", "m7(b5)", "1-b3-x"}:
        roman = roman.lower()
    if chord_name in {"°", "m7(b5)"}:
        roman += "°"
    elif chord_name == "+":
        roman += "+"
    return roman


def _annotate_harmonic_function(chords: list[dict[str, Any]], key_estimate: dict[str, Any] | None) -> None:
    if not key_estimate or key_estimate["confidence"] < KEY_CONFIDENCE_THRESHOLD:
        return
    key_root = int(key_estimate["root"])
    for chord in chords:
        interval = (int(chord["root"]) - key_root) % 12
        chord["roman"] = _roman_numeral(int(chord["root"]), str(chord["name"]), key_root)
        chord["function"] = HARMONIC_FUNCTIONS.get(interval, "chromatic")


def _contract_version_error(expected_version: str, actual_version: str) -> dict[str, Any]:
    return {
        "status": "error",
        "error": {
            "code": "deep_research_contract_incompatible",
            "expected_contract_version": expected_version,
            "actual_contract_version": actual_version,
            "message": "Deep research contract version is incompatible; staged rollout required.",
        },
    }


def run_research(contract: ResearchContract) -> dict[str, Any]:
    """Track B logic: consumes only frozen contract input."""
    expected_version = current_contract_version()
    if not contract_versions_compatible(expected_version, contract.contract_version):
        return _contract_version_error(expected_version, contract.contract_version)

    transport = dict(contract.transport)
    active_notes = dict(contract.active_notes)
    flattened = [note for notes in active_notes.values() for note in notes]
    active_total = len(flattened)

    if active_total <= 1:
        density = "sparse"
    elif active_total <= 4:
        density = "medium"
    else:
        density = "dense"

    tick = int(transport.get("tick", 0))
    motif_span = 0 if not flattened else (max(flattened) - min(flattened) + (tick % 3))
    chord_candidates = _build_chord_candidates(flattened)
    key_estimate = _compute_key_estimate(flattened)
    _annotate_harmonic_function(chord_candidates, key_estimate)

    return ResearchResult(
        motif_span=motif_span,
        note_density=density,
        active_note_total=active_total,
        chord_candidates=chord_candidates,
        key_estimate=(
            None
            if key_estimate is None
            else {
                "label": key_estimate["label"],
                "confidence": key_estimate["confidence"],
                "alternatives": key_estimate["alternatives"],
            }
        ),
    ).as_dict()
