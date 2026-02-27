from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {"engine.adapters.legacy_page_events", "ui.adapters"}
SELF = Path("tools/check_legacy_adapter_dependencies.py")


def python_files() -> list[Path]:
    return [
        path for path in ROOT.rglob("*.py")
        if ".git" not in path.parts and ".venv" not in path.parts and path.relative_to(ROOT) != SELF
    ]


def scan_file(path: Path) -> list[str]:
    found: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name in FORBIDDEN or any(name.startswith(f"{item}.") for item in FORBIDDEN):
                    found.append(name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in FORBIDDEN or any(module.startswith(f"{item}.") for item in FORBIDDEN):
                found.append(module)
    return found


def main() -> int:
    violations: list[tuple[str, list[str]]] = []
    for path in python_files():
        rel = path.relative_to(ROOT)
        try:
            imports = scan_file(path)
        except Exception:
            continue
        if imports:
            violations.append((rel.as_posix(), sorted(set(imports))))

    if violations:
        print("Forbidden legacy adapter dependencies found:")
        for rel, imports in violations:
            print(f" - {rel}: {', '.join(imports)}")
        return 1

    print("No forbidden legacy adapter dependencies found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
