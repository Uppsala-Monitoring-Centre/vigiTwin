"""Pre-extract per-report narrative demographics into a frozen bundle.

Run once at frozen-dataset creation time. The output JSON is loaded by
``PipelineRunner`` via ``frozen_narrative_demographics=`` so that
inference-time runs can be fully deterministic — no LLM client required.

See ``docs/integration_guide.md`` § *Wiring without an LLM client*.

Usage::

    python scripts/extract_frozen_narrative_demographics.py \\
        --reports path/to/validation_reports.json \\
        --llm-factory my_secure_pkg.llm:make_client \\
        --out path/to/narrative_demographics.json

The ``--reports`` file is a JSON list of ``Report`` objects (i.e. the
output of ``json.dumps([r.model_dump() for r in reports])``).

The ``--llm-factory`` flag is a ``module:callable`` reference. The
callable is imported and invoked with no arguments; it must return an
object satisfying the ``LLMClient`` protocol.
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
    FrozenNarrativeDemographics,
    save_frozen_narrative_demographics,
)
from llm_dup_dev.llm.protocols import LLMClient
from llm_dup_dev.llm.tasks.narrative_demographics import (
    extract_narrative_demographics,
)
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
        help="Output path for the FrozenNarrativeDemographics JSON.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help=(
            "Sampling temperature. Omitted by default, so the backend "
            "applies its own default — required by models that reject an "
            "explicit temperature, and not generally deterministic. Pass "
            "0.0 for a reproducible bundle, which requires a model that "
            "accepts it."
        ),
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

    extractions: dict[str, object] = {}
    for report in reports:
        if report.report_id in extractions:
            logger.warning(
                "duplicate report_id %s in %s; keeping first occurrence",
                report.report_id,
                args.reports,
            )
            continue
        logger.info("extracting demographics for %s", report.report_id)
        extractions[report.report_id] = extract_narrative_demographics(
            client, report.report_id, report.narrative,
            temperature=args.temperature,
        )

    bundle = FrozenNarrativeDemographics(extractions=extractions)
    save_frozen_narrative_demographics(bundle, args.out)
    print(
        f"wrote {len(extractions)} extraction(s) to {args.out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
