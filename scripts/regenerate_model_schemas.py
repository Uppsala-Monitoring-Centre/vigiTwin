"""Rewrite ``schemas/*.schema.json`` for every model the drift test checks.

The model registry is ``tests.test_schemas_examples.MODEL_MAP`` — imported,
not copied. That test is the oracle for whether a committed schema is current,
so a regenerator carrying its own list could satisfy itself while leaving the
test red. This one cannot: there is a single list, and adding a model to the
test picks it up here for free.

Schemas only. The three ``generate_*.py`` scripts beside this one own the
*computed* examples and the ``study_*`` schemas, which are not in MODEL_MAP;
run those as well. See ``docs/integration_guide.md`` § Public surface for what
the schemas are a contract for.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_schemas_examples import MODEL_MAP, SCHEMAS_DIR  # noqa: E402


def main() -> None:
    for name, model in MODEL_MAP.items():
        path = SCHEMAS_DIR / f"{name}.schema.json"
        path.write_text(
            json.dumps(model.model_json_schema(), indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
