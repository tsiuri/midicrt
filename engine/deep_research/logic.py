from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any

from harmony import CHORDS, NOTE_NAMES

from .platform import (
    ResearchContract,
    contract_versions_compatible,
    current_contract_version,
    thaw_payload,
)


@dataclass(frozen=True)
class ResearchResult:
    motif_span: int
    note_density: str
    active_note_total: int
    chord_candidates: list[dict[str, Any]]
    key_estimate: dict[str, Any] | None
    microtiming: dict[str, Any]
    time_signature: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "motif_span": self.motif_span,
            "note_density": self.note_density,
            "active_note_total": self.active_note_total,
            "chord_candidates": self.chord_candidates,
            "key_estimate": self.key_estimate,
            "microtiming": self.microtiming,
            "time_signature": self.time_signature,
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


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _extract_microtiming_events(transport: dict[str, Any]) -> list[dict[str, Any]]:
    microtiming = transport.get("microtiming")
    if not isinstance(microtiming, dict):
        return []
    note_on_events = microtiming.get("note_on_events")
    if not isinstance(note_on_events, list):
        return []
    normalized: list[dict[str, Any]] = []
    for event in note_on_events:
        if not isinstance(event, dict):
            continue
        tick = _coerce_float(event.get("tick"))
        channel = event.get("channel", 0)
        if tick is None or not isinstance(channel, int):
            continue
        normalized.append({"tick": tick, "channel": channel})
    return normalized


def _compute_microtiming(transport: dict[str, Any]) -> dict[str, Any]:
    timing = transport.get("timing")
    ticks_per_beat = 24.0
    subdivision = 4
    if isinstance(timing, dict):
        ticks_per_beat = max(1.0, _coerce_float(timing.get("ticks_per_beat")) or ticks_per_beat)
        subdivision = int(max(1, _coerce_float(timing.get("subdivision")) or subdivision))

    events = _extract_microtiming_events(transport)
    bucket_counts: dict[str, int] = {
        "early": 0,
        "on_grid": 0,
        "late": 0,
    }
    channel_offsets: dict[str, list[float]] = {}
    offsets: list[float] = []
    step = ticks_per_beat / subdivision

    for event in events:
        tick = event["tick"]
        channel_key = str(event["channel"])
        nearest_step = round(tick / step)
        offset = tick - (nearest_step * step)
        offset = _round4(offset)
        offsets.append(offset)
        channel_offsets.setdefault(channel_key, []).append(offset)
        if abs(offset) <= (step * 0.05):
            bucket_counts["on_grid"] += 1
        elif offset < 0:
            bucket_counts["early"] += 1
        else:
            bucket_counts["late"] += 1

    per_channel = {
        channel: {
            "count": len(items),
            "mean_offset": _round4(sum(items) / len(items)),
            "median_offset": _round4(float(median(items))),
        }
        for channel, items in sorted(channel_offsets.items(), key=lambda pair: int(pair[0]))
    }

    mean_offset = 0.0 if not offsets else _round4(sum(offsets) / len(offsets))
    median_offset = 0.0 if not offsets else _round4(float(median(offsets)))
    return {
        "subdivision": subdivision,
        "ticks_per_beat": _round4(ticks_per_beat),
        "histogram": bucket_counts,
        "aggregate": {
            "count": len(offsets),
            "mean_offset": mean_offset,
            "median_offset": median_offset,
        },
        "per_channel": per_channel,
    }


def _as_signature(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    label = value.strip()
    if "/" not in label:
        return None
    beats, denom = label.split("/", 1)
    if not beats.isdigit() or not denom.isdigit():
        return None
    return f"{int(beats)}/{int(denom)}"


def _as_positive_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    return 0


def _extract_timesig_candidate(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    current = _as_signature(payload.get("current_signature"))
    if current is None:
        for field in ("signature", "meter_estimate", "label"):
            current = _as_signature(payload.get(field))
            if current:
                break
    if current is None:
        labels = payload.get("labels")
        if isinstance(labels, list):
            for label in labels:
                current = _as_signature(label)
                if current:
                    break

    pending = _as_signature(payload.get("pending_change"))
    if pending is None:
        pending_value = payload.get("pending")
        if isinstance(pending_value, dict):
            pending = _as_signature(pending_value.get("signature"))
            if pending is None:
                p_labels = pending_value.get("labels")
                if isinstance(p_labels, list):
                    for label in p_labels:
                        pending = _as_signature(label)
                        if pending:
                            break
    if pending is None:
        for field in ("pending_signature", "next_signature"):
            pending = _as_signature(payload.get(field))
            if pending:
                break
        if pending is None:
            p_labels = payload.get("pending_labels")
            if isinstance(p_labels, list):
                for label in p_labels:
                    pending = _as_signature(label)
                    if pending:
                        break

    confidence = _coerce_float(payload.get("confidence")) or 0.0
    stability = 0
    for field in ("stability_window", "window", "window_events", "stable_ticks"):
        stability = _as_positive_int(payload.get(field))
        if stability > 0:
            break

    if current is None and pending is None and confidence <= 0.0 and stability == 0:
        return None

    return {
        "current_signature": current,
        "confidence": max(0.0, min(1.0, confidence)),
        "stability_window": stability,
        "pending_change": pending,
    }


def _compute_time_signature(transport: dict[str, Any], module_outputs: dict[str, Any]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for source in (
        transport.get("timesig"),
        transport.get("timesig_exp"),
        module_outputs.get("timesig"),
        module_outputs.get("timesig_exp"),
    ):
        candidate = _extract_timesig_candidate(source)
        if candidate:
            candidates.append(candidate)

    for field in ("meter_estimate", "signature"):
        label = _as_signature(transport.get(field))
        if label:
            candidates.append(
                {
                    "current_signature": label,
                    "confidence": max(0.0, min(1.0, _coerce_float(transport.get("confidence")) or 0.0)),
                    "stability_window": _as_positive_int(transport.get("stability_window")),
                    "pending_change": _as_signature(transport.get("pending_change")),
                }
            )
            break

    best = max(candidates, key=lambda item: (item["confidence"], item["stability_window"]), default=None)
    if best is None:
        return {
            "current_signature": "4/4",
            "confidence": 0.0,
            "stability_window": 0,
            "pending_change": None,
        }

    current_signature = best["current_signature"] or "4/4"
    return {
        "current_signature": current_signature,
        "confidence": _round4(float(best["confidence"])),
        "stability_window": int(best["stability_window"]),
        "pending_change": best["pending_change"],
    }


def run_research(contract: ResearchContract) -> dict[str, Any]:
    """Track B logic: consumes only frozen contract input."""
    expected_version = current_contract_version()
    if not contract_versions_compatible(expected_version, contract.contract_version):
        return _contract_version_error(expected_version, contract.contract_version)

    transport = thaw_payload(contract.transport)
    module_outputs = thaw_payload(contract.module_outputs)
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
    microtiming = _compute_microtiming(transport)
    time_signature = _compute_time_signature(transport, module_outputs if isinstance(module_outputs, dict) else {})

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
        microtiming=microtiming,
        time_signature=time_signature,
    ).as_dict()
