"""``characterization`` is a leaf: nothing else in the package may import it.

The dependency runs one way — ``characterization`` reads
``study_eval.schemas``, ``models``, ``feature_generation`` and ``data`` — and
must keep running one way. Two reasons:

* ``study_eval`` deliberately never handles ``Report`` objects. Its whole input
  contract is pair ids, labels and predictions, and an import from here would be
  the first step towards report content entering the performance analysis.
* ``characterization`` already imports ``study_eval.schemas``, so an import back
  the other way is a cycle waiting to happen.

Both are the kind of constraint that a reviewer reads past. A test does not.
"""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "llm_dup_dev"
PACKAGE = "llm_dup_dev.characterization"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module)
    return imported


def test_no_module_outside_characterization_imports_it():
    offenders: list[str] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        if PACKAGE.split(".")[-1] in path.relative_to(SOURCE_ROOT).parts:
            continue
        if any(module.startswith(PACKAGE) for module in _imported_modules(path)):
            offenders.append(str(path.relative_to(SOURCE_ROOT)))

    assert offenders == [], (
        f"{offenders} import {PACKAGE}. It is a leaf package: reports must not "
        f"reach study_eval, and the reverse import would create a cycle."
    )


def test_characterization_does_not_import_the_study_eval_package_root():
    """Submodules only — the root pulls in the entire four-step workflow."""
    offenders: list[str] = []
    for path in (SOURCE_ROOT / "characterization").rglob("*.py"):
        if "llm_dup_dev.study_eval" in _imported_modules(path):
            offenders.append(str(path.relative_to(SOURCE_ROOT)))

    assert offenders == []
