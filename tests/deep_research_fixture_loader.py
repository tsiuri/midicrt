import json
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "deep_research_sequences"
REQUIRED_KEYS = {
    "name",
    "schema_version",
    "event",
    "transport",
    "active_notes",
    "expected",
}
_ALLOWED_EVENT_KINDS = {"clock", "note_on", "note_off"}


class FixtureValidationError(ValueError):
    """Raised when deep research sequence fixtures fail deterministic validation."""


def discover_deep_research_sequence_fixture_paths() -> list[Path]:
    return sorted(FIXTURE_DIR.glob("*.json"), key=lambda path: path.name)


def load_deep_research_sequence_fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _require_keys(case: dict[str, Any], fixture_path: Path) -> None:
    missing = REQUIRED_KEYS.difference(case)
    if missing:
        missing_keys = ", ".join(sorted(missing))
        raise FixtureValidationError(f"{fixture_path.name}: missing required keys: {missing_keys}")


def validate_deep_research_sequence_fixture(case: dict[str, Any], fixture_path: Path) -> None:
    if not isinstance(case, dict):
        raise FixtureValidationError(f"{fixture_path.name}: fixture payload must be an object")

    _require_keys(case, fixture_path)

    if not isinstance(case["name"], str) or not case["name"]:
        raise FixtureValidationError(f"{fixture_path.name}: name must be a non-empty string")

    if not isinstance(case["schema_version"], int):
        raise FixtureValidationError(f"{fixture_path.name}: schema_version must be an int")

    event = case["event"]
    if not isinstance(event, dict) or not isinstance(event.get("kind"), str):
        raise FixtureValidationError(f"{fixture_path.name}: event.kind must be a string")
    if event["kind"] not in _ALLOWED_EVENT_KINDS:
        allowed = ", ".join(sorted(_ALLOWED_EVENT_KINDS))
        raise FixtureValidationError(
            f"{fixture_path.name}: event.kind must be one of: {allowed}"
        )

    transport = case["transport"]
    if not isinstance(transport, dict):
        raise FixtureValidationError(f"{fixture_path.name}: transport must be an object")
    for field in ("tick", "bar"):
        if not isinstance(transport.get(field), int):
            raise FixtureValidationError(f"{fixture_path.name}: transport.{field} must be an int")
    if not isinstance(transport.get("running"), bool):
        raise FixtureValidationError(f"{fixture_path.name}: transport.running must be a bool")
    if not isinstance(transport.get("bpm"), (int, float)):
        raise FixtureValidationError(f"{fixture_path.name}: transport.bpm must be numeric")

    active_notes = case["active_notes"]
    if not isinstance(active_notes, dict):
        raise FixtureValidationError(f"{fixture_path.name}: active_notes must be an object")
    for channel, notes in active_notes.items():
        if not isinstance(channel, str):
            raise FixtureValidationError(f"{fixture_path.name}: active_notes channel keys must be strings")
        if not isinstance(notes, list) or not all(isinstance(note, int) for note in notes):
            raise FixtureValidationError(
                f"{fixture_path.name}: active_notes[{channel!r}] must be a list of ints"
            )

    expected = case["expected"]
    if not isinstance(expected, dict):
        raise FixtureValidationError(f"{fixture_path.name}: expected must be an object")
    for field in ("motif_span", "active_note_total"):
        if not isinstance(expected.get(field), int):
            raise FixtureValidationError(f"{fixture_path.name}: expected.{field} must be an int")
    if not isinstance(expected.get("note_density"), str):
        raise FixtureValidationError(f"{fixture_path.name}: expected.note_density must be a string")

    microtiming = expected.get("microtiming")
    if not isinstance(microtiming, dict):
        raise FixtureValidationError(f"{fixture_path.name}: expected.microtiming must be an object")
    for field in ("subdivision",):
        if not isinstance(microtiming.get(field), int):
            raise FixtureValidationError(f"{fixture_path.name}: expected.microtiming.{field} must be an int")
    if not isinstance(microtiming.get("ticks_per_beat"), (int, float)):
        raise FixtureValidationError(
            f"{fixture_path.name}: expected.microtiming.ticks_per_beat must be numeric"
        )
    histogram = microtiming.get("histogram")
    if not isinstance(histogram, dict):
        raise FixtureValidationError(f"{fixture_path.name}: expected.microtiming.histogram must be an object")
    for field in ("early", "on_grid", "late"):
        if not isinstance(histogram.get(field), int):
            raise FixtureValidationError(
                f"{fixture_path.name}: expected.microtiming.histogram.{field} must be an int"
            )

    chord_candidates = expected.get("chord_candidates")
    if not isinstance(chord_candidates, list):
        raise FixtureValidationError(
            f"{fixture_path.name}: expected.chord_candidates must be a list"
        )
    for index, candidate in enumerate(chord_candidates):
        if not isinstance(candidate, dict):
            raise FixtureValidationError(
                f"{fixture_path.name}: expected.chord_candidates[{index}] must be an object"
            )
        for field in ("label", "name"):
            if not isinstance(candidate.get(field), str):
                raise FixtureValidationError(
                    f"{fixture_path.name}: expected.chord_candidates[{index}].{field} must be a string"
                )
        if not isinstance(candidate.get("root"), int):
            raise FixtureValidationError(
                f"{fixture_path.name}: expected.chord_candidates[{index}].root must be an int"
            )
        if not isinstance(candidate.get("confidence"), (int, float)):
            raise FixtureValidationError(
                f"{fixture_path.name}: expected.chord_candidates[{index}].confidence must be numeric"
            )
        if not isinstance(candidate.get("missing_tones"), list):
            raise FixtureValidationError(
                f"{fixture_path.name}: expected.chord_candidates[{index}].missing_tones must be a list"
            )
        if not all(isinstance(tone, str) for tone in candidate["missing_tones"]):
            raise FixtureValidationError(
                f"{fixture_path.name}: expected.chord_candidates[{index}].missing_tones entries must be strings"
            )
        for optional in ("roman", "function"):
            if optional in candidate and not isinstance(candidate.get(optional), str):
                raise FixtureValidationError(
                    f"{fixture_path.name}: expected.chord_candidates[{index}].{optional} must be a string"
                )


    time_signature = expected.get("time_signature")
    if not isinstance(time_signature, dict):
        raise FixtureValidationError(f"{fixture_path.name}: expected.time_signature must be an object")
    if not isinstance(time_signature.get("current_signature"), str):
        raise FixtureValidationError(
            f"{fixture_path.name}: expected.time_signature.current_signature must be a string"
        )
    if not isinstance(time_signature.get("confidence"), (int, float)):
        raise FixtureValidationError(
            f"{fixture_path.name}: expected.time_signature.confidence must be numeric"
        )
    if not isinstance(time_signature.get("stability_window"), int):
        raise FixtureValidationError(
            f"{fixture_path.name}: expected.time_signature.stability_window must be an int"
        )
    pending_change = time_signature.get("pending_change")
    if pending_change is not None and not isinstance(pending_change, str):
        raise FixtureValidationError(
            f"{fixture_path.name}: expected.time_signature.pending_change must be a string or null"
        )

    key_estimate = expected.get("key_estimate")
    if key_estimate is not None:
        if not isinstance(key_estimate, dict):
            raise FixtureValidationError(f"{fixture_path.name}: expected.key_estimate must be an object or null")
        if not isinstance(key_estimate.get("label"), str):
            raise FixtureValidationError(f"{fixture_path.name}: expected.key_estimate.label must be a string")
        if not isinstance(key_estimate.get("confidence"), (int, float)):
            raise FixtureValidationError(
                f"{fixture_path.name}: expected.key_estimate.confidence must be numeric"
            )
        alternatives = key_estimate.get("alternatives")
        if not isinstance(alternatives, list):
            raise FixtureValidationError(
                f"{fixture_path.name}: expected.key_estimate.alternatives must be a list"
            )
        for index, alternative in enumerate(alternatives):
            if not isinstance(alternative, dict):
                raise FixtureValidationError(
                    f"{fixture_path.name}: expected.key_estimate.alternatives[{index}] must be an object"
                )
            if not isinstance(alternative.get("label"), str):
                raise FixtureValidationError(
                    f"{fixture_path.name}: expected.key_estimate.alternatives[{index}].label must be a string"
                )
            if not isinstance(alternative.get("confidence"), (int, float)):
                raise FixtureValidationError(
                    f"{fixture_path.name}: expected.key_estimate.alternatives[{index}].confidence must be numeric"
                )


def load_all_deep_research_sequence_fixtures() -> list[dict[str, Any]]:
    fixtures: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for fixture_path in discover_deep_research_sequence_fixture_paths():
        case = load_deep_research_sequence_fixture(fixture_path)
        validate_deep_research_sequence_fixture(case, fixture_path)
        name = case["name"]
        if name in seen_names:
            raise FixtureValidationError(
                f"{fixture_path.name}: duplicate fixture name detected: {name}"
            )
        seen_names.add(name)
        fixtures.append(case)

    return fixtures
