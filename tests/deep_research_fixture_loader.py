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
