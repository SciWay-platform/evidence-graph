#!/usr/bin/env python3
"""Fail if evidence-graph has grown a runtime dependency.

The zero-dependency property is a design constraint, not a coincidence: the library must drop into
any environment (a stdlib-only backend, an air-gapped box, someone else's pipeline) without pulling
a tree behind it. Anything that needs a model, a network call, or a credential belongs in the
caller, injected through a Protocol.

Two checks, because the declaration and the code can disagree:

  1. `[project].dependencies` in pyproject.toml is empty.
  2. Every module in the package imports only the standard library or evidence_graph itself. This is
     the check that actually bites — a stray `import requests` passes a test suite on a developer
     machine where requests happens to be installed, and fails for the user.

Run: python scripts/check_no_runtime_deps.py
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "evidence_graph"
SRC = ROOT / PACKAGE


def _declared_dependencies() -> list[str]:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh).get("project", {}).get("dependencies", [])


def _imported_roots(path: Path) -> set[str]:
    """Top-level module names imported by one file (`import a.b` and `from a.b import c`)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` has no module root to check; it is internal by construction.
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def main() -> int:
    problems: list[str] = []

    declared = _declared_dependencies()
    if declared:
        problems.append(f"pyproject declares runtime dependencies: {declared}")

    allowed = set(sys.stdlib_module_names) | {PACKAGE}
    for path in sorted(SRC.rglob("*.py")):
        for root in sorted(_imported_roots(path) - allowed):
            problems.append(f"{path.relative_to(ROOT)} imports third-party module {root!r}")

    if problems:
        print("Zero-dependency constraint violated:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nIf the dependency is genuinely required, it belongs behind a Protocol in the caller."
            "\nSee the 'Scope' section of CONTRIBUTING.md.",
            file=sys.stderr,
        )
        return 1

    checked = len(list(SRC.rglob("*.py")))
    print(f"OK: no runtime dependencies declared, {checked} module(s) import stdlib only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
