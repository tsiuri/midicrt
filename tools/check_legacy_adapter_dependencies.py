from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {"engine.adapters.legacy_page_events", "ui.adapters"}
EXCLUDE = {
    Path("tools/check_legacy_adapter_dependencies.py"),
}


def _iter_py_files() -> list[Path]:
    return [p for p in ROOT.rglob("*.py") if ".venv" not in p.parts and p.relative_to(ROOT) not in EXCLUDE]


def _imports_target(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name in TARGETS or any(name.startswith(f"{target}.") for target in TARGETS):
                    found.append(name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in TARGETS or any(module.startswith(f"{target}.") for target in TARGETS):
                found.append(module)
    return found


def main() -> int:
    violations: list[tuple[Path, list[str]]] = []
    for py_file in _iter_py_files():
        rel = py_file.relative_to(ROOT)
        if rel.as_posix().startswith(".git/"):
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        found = _imports_target(tree)
        if found:
            violations.append((rel, found))

    if violations:
        print("Found forbidden legacy adapter dependencies:")
        for rel, mods in violations:
            uniq = ", ".join(sorted(set(mods)))
            print(f" - {rel}: {uniq}")
        return 1
    print("No forbidden legacy adapter dependencies found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
