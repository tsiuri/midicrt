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


def discover_deep_research_sequence_fixture_paths() -> list[Path]:
    return sorted(FIXTURE_DIR.glob("*.json"), key=lambda path: path.name)


def load_deep_research_sequence_fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())
