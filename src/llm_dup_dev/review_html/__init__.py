"""Reusable HTML review renderer for suspected duplicate report pairs.

This package generates standalone HTML files presenting two reports
side by side, optimised for human inspection, annotation, and
precision-testing workflows.

Quick start
-----------
Render a single pair from Python::

    from llm_dup_dev.review_html import render_pair_html

    path = render_pair_html(report_1, report_2, pair_id="R001__R002")

Render a batch::

    from llm_dup_dev.review_html import render_batch_html, PairRenderInput

    items = [
        PairRenderInput(report_1=r1, report_2=r2, pair_id="R001__R002"),
        PairRenderInput(report_1=r3, report_2=r4, pair_id="R003__R004"),
    ]
    output_dir = render_batch_html(items, output_dir=Path("outputs/review"))

CLI usage::

    python -m llm_dup_dev.review_html \\
        --reports reports.json --pairs pairs.json --output-dir outputs/review/

Input contract
--------------
All inputs are frozen data — ``Report``, ``PairFeatures``, and
``ClassificationResult`` Pydantic models. No database access is required.

The secure repo only needs thin wrapper code to load frozen artifacts
and call ``render_pair_html`` or ``render_batch_html``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from llm_dup_dev.data.offline import FrozenPairBundle
from llm_dup_dev.models.classification import ClassificationResult
from llm_dup_dev.models.narrative_demographics import NarrativeDemographics
from llm_dup_dev.models.pair_features import PairFeatures
from llm_dup_dev.models.report import Report
from llm_dup_dev.pipeline.runner import PairResult

from .compare import build_pair_view_model
from .models import (
    AnnotationVM,
    BatchIndexItem,
    ComparisonStatus,
    DecisionSummaryVM,
    PairViewModel,
)
from .render import DEFAULT_OUTPUT_DIR, write_batch_html, write_pair_html


@dataclass
class PairRenderInput:
    """Bundle of inputs for rendering one pair.

    Only ``report_1``, ``report_2``, and ``pair_id`` are required.
    """

    report_1: Report
    report_2: Report
    pair_id: str
    features: PairFeatures | None = None
    classification: ClassificationResult | None = None
    external_url: str | None = None
    external_url_label: str = "External link"
    frozen_bundle: FrozenPairBundle | None = None
    annotation: dict | None = None
    narrative_demographics_1: NarrativeDemographics | None = None
    narrative_demographics_2: NarrativeDemographics | None = None


def render_pair_html(
    report_1: Report,
    report_2: Report,
    pair_id: str,
    *,
    features: PairFeatures | None = None,
    classification: ClassificationResult | None = None,
    external_url: str | None = None,
    external_url_label: str = "External link",
    frozen_bundle: FrozenPairBundle | None = None,
    include_debug: bool = False,
    annotation: dict | None = None,
    narrative_demographics_1: NarrativeDemographics | None = None,
    narrative_demographics_2: NarrativeDemographics | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> Path:
    """Render a single pair to a standalone HTML file.

    Parameters
    ----------
    report_1, report_2 : Report
        The two reports to compare.
    pair_id : str
        Identifier for the pair.
    features : PairFeatures, optional
        Pre-computed pipeline features for the evidence section.
    classification : ClassificationResult, optional
        Pipeline classification result.
    external_url : str, optional
        URL to render as a header link (e.g. a Vigilyze link).
    external_url_label : str
        Human-readable label for the external URL link.
    frozen_bundle : FrozenPairBundle, optional
        Frozen per-pair frequency statistics.  When provided, a
        Frequency Statistics section is rendered in the HTML report.
    include_debug : bool
        When True, includes collapsible raw JSON sections for
        PairFeatures and ClassificationResult.
    annotation : dict, optional
        A human annotation dict with at least a
        ``"duplicates_assessment"`` key.  Shows the human label
        alongside the pipeline classification.
    output_dir : Path
        Directory to write into (created if absent).

    Returns
    -------
    Path
        Path to the written HTML file.
    """
    vm = build_pair_view_model(
        report_1=report_1,
        report_2=report_2,
        pair_id=pair_id,
        features=features,
        classification=classification,
        external_url=external_url,
        external_url_label=external_url_label,
        frozen_bundle=frozen_bundle,
        include_debug=include_debug,
        annotation=annotation,
        narrative_demographics_1=narrative_demographics_1,
        narrative_demographics_2=narrative_demographics_2,
    )
    return write_pair_html(vm, output_dir)


def render_batch_html(
    items: Sequence[PairRenderInput],
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    write_index: bool = True,
) -> Path:
    """Render a batch of pairs to standalone HTML files.

    Parameters
    ----------
    items : Sequence[PairRenderInput]
        One entry per pair.  Set ``item.frozen_bundle`` to include
        frequency statistics in the rendered page for that pair.
    output_dir : Path
        Directory to write into (created if absent).
    write_index : bool
        Whether to generate an ``index.html`` linking to all pairs.

    Returns
    -------
    Path
        The output directory.
    """
    view_models = [
        build_pair_view_model(
            report_1=item.report_1,
            report_2=item.report_2,
            pair_id=item.pair_id,
            features=item.features,
            classification=item.classification,
            external_url=item.external_url,
            external_url_label=item.external_url_label,
            frozen_bundle=item.frozen_bundle,
            annotation=item.annotation,
            narrative_demographics_1=item.narrative_demographics_1,
            narrative_demographics_2=item.narrative_demographics_2,
        )
        for item in items
    ]
    return write_batch_html(
        view_models, output_dir, write_index=write_index
    )


# -------------------------------------------------------------------
# Prediction/evaluation rendering (PairResult-based)
# -------------------------------------------------------------------


@dataclass
class ResultRenderInput:
    """Bundle of inputs for rendering one evaluated pipeline result.

    Requires both the pipeline result and the original reports so
    that the side-by-side comparison can be rendered alongside the
    prediction/evaluation sections.
    """

    result: PairResult
    report_1: Report
    report_2: Report
    external_url: str | None = None
    external_url_label: str = "External link"
    frozen_bundle: FrozenPairBundle | None = None
    include_debug: bool = False
    annotation: dict | None = None


def render_result_html(
    result: PairResult,
    report_1: Report,
    report_2: Report,
    *,
    external_url: str | None = None,
    external_url_label: str = "External link",
    frozen_bundle: FrozenPairBundle | None = None,
    include_debug: bool = False,
    annotation: dict | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> Path:
    """Render an evaluated pipeline result to a standalone HTML file.

    This reuses the same side-by-side comparison layout as
    ``render_pair_html`` but adds prediction/evaluation sections:
    decision summary, detailed evidence panels, and an optional
    collapsible debug section.

    Parameters
    ----------
    result : PairResult
        Pipeline result containing features and classification.
    report_1, report_2 : Report
        The two reports to compare.
    external_url : str, optional
        URL to render as a header link.
    external_url_label : str
        Human-readable label for the external URL link.
    frozen_bundle : FrozenPairBundle, optional
        Frozen per-pair frequency statistics.
    include_debug : bool
        When True, includes collapsible raw JSON sections for
        PairFeatures and ClassificationResult.
    output_dir : Path
        Directory to write into (created if absent).

    Returns
    -------
    Path
        Path to the written HTML file.
    """
    vm = build_pair_view_model(
        report_1=report_1,
        report_2=report_2,
        pair_id=result.pair_id,
        features=result.features,
        classification=result.classification,
        external_url=external_url,
        external_url_label=external_url_label,
        frozen_bundle=frozen_bundle,
        include_debug=include_debug,
        annotation=annotation,
        narrative_demographics_1=result.narrative_demographics_1,
        narrative_demographics_2=result.narrative_demographics_2,
    )
    return write_pair_html(vm, output_dir)


def render_results_html(
    items: Sequence[ResultRenderInput],
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    write_index: bool = True,
) -> Path:
    """Render a batch of evaluated pipeline results to HTML files.

    Parameters
    ----------
    items : Sequence[ResultRenderInput]
        One entry per evaluated pair.
    output_dir : Path
        Directory to write into (created if absent).
    write_index : bool
        Whether to generate an ``index.html`` linking to all pairs.

    Returns
    -------
    Path
        The output directory.
    """
    view_models = [
        build_pair_view_model(
            report_1=item.report_1,
            report_2=item.report_2,
            pair_id=item.result.pair_id,
            features=item.result.features,
            classification=item.result.classification,
            external_url=item.external_url,
            external_url_label=item.external_url_label,
            frozen_bundle=item.frozen_bundle,
            include_debug=item.include_debug,
            annotation=item.annotation,
            narrative_demographics_1=item.result.narrative_demographics_1,
            narrative_demographics_2=item.result.narrative_demographics_2,
        )
        for item in items
    ]
    return write_batch_html(
        view_models, output_dir, write_index=write_index
    )


# Imported last: saved_run depends on ResultRenderInput / render_results_html
# defined above, and imports them back via ``from . import ...``.
from .saved_run import (  # noqa: E402
    SavedRunSummary,
    load_saved_run,
    render_saved_run,
)

__all__ = [
    "AnnotationVM",
    "BatchIndexItem",
    "ComparisonStatus",
    "DecisionSummaryVM",
    "FrozenPairBundle",
    "PairRenderInput",
    "PairResult",
    "PairViewModel",
    "ResultRenderInput",
    "SavedRunSummary",
    "load_saved_run",
    "render_batch_html",
    "render_pair_html",
    "render_result_html",
    "render_results_html",
    "render_saved_run",
]
