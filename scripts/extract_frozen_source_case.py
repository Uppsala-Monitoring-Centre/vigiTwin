"""Pre-extract per-report narrative source/case signals into a frozen bundle.

Run once at frozen-dataset creation time. The output JSON is loaded by
``PipelineRunner`` via ``frozen_source_case_extractions=`` so that
inference-time runs can be fully deterministic — no LLM client required.

See ``docs/integration_guide.md`` § *Wiring without an LLM client*.

Usage::

    python scripts/extract_frozen_source_case.py \\
        --reports path/to/validation_reports.json \\
        --llm-factory my_secure_pkg.llm:make_client \\
        --out path/to/source_case.json

The ``--reports`` file is a JSON list of ``Report`` objects (i.e. the
output of ``json.dumps([r.model_dump() for r in reports])``).

The ``--llm-factory`` flag is a ``module:callable`` reference. The
callable is imported and invoked with no arguments; it must return an
object satisfying the ``LLMClient`` protocol. The dev repo never
instantiates a concrete client itself — secure-side wiring supplies
the factory.
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import sys
from pathlib import Path
from typing import Callable

from llm_dup_dev.data.offline import (
    FrozenSourceCaseExtractions,
    save_frozen_source_case_extractions,
)
from llm_dup_dev.llm.protocols import LLMClient
from llm_dup_dev.llm.tasks.narrative_source_case import extract_source_case
from llm_dup_dev.models.report import Report

logger = logging.getLogger(__name__)


def _load_factory(spec: str) -> Callable[[], LLMClient]:
    """Resolve ``module:callable`` to a zero-arg callable returning an LLMClient."""
    if ":" not in spec:
        raise ValueError(
            f"--llm-factory must be of the form 'module:callable', got {spec!r}"
        )
    module_name, attr = spec.split(":", 1)
    module = importlib.import_module(module_name)
    factory = getattr(module, attr)
    if not callable(factory):
        raise TypeError(
            f"{spec!r} resolved to {factory!r}, which is not callable"
        )
    return factory


def _load_reports(path: Path) -> list[Report]:
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise ValueError(
            f"{path}: expected a JSON array of Report objects, got "
            f"{type(raw).__name__}"
        )
    return [Report.model_validate(item) for item in raw]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--reports",
        required=True,
        type=Path,
        help="Path to a JSON array of Report objects.",
    )
    parser.add_argument(
        "--llm-factory",
        required=True,
        help="'module:callable' returning an LLMClient (zero-arg).",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Output path for the FrozenSourceCaseExtractions JSON.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Log per-report progress.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    reports = _load_reports(args.reports)
    factory = _load_factory(args.llm_factory)
    client = factory()

    # Deduplicate by report_id — extracting the same narrative twice would
    # waste tokens and produce a bundle that overwrites itself. The first
    # occurrence wins; later duplicates are skipped with a warning.
    extractions: dict[str, object] = {}
    for report in reports:
        if report.report_id in extractions:
            logger.warning(
                "duplicate report_id %s in %s; keeping first occurrence",
                report.report_id,
                args.reports,
            )
            continue
        logger.info("extracting source/case for %s", report.report_id)
        extractions[report.report_id] = extract_source_case(
            client, report.report_id, report.narrative
        )

    bundle = FrozenSourceCaseExtractions(extractions=extractions)
    save_frozen_source_case_extractions(bundle, args.out)
    print(
        f"wrote {len(extractions)} extraction(s) to {args.out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
