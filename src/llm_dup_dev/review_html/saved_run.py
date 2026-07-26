"""Load a saved evaluation run from disk and render it to HTML.

A pipeline run persists its output as JSON artifacts (see
:mod:`llm_dup_dev.pipeline.export`). This module reads those artifacts back
into :class:`~llm_dup_dev.review_html.ResultRenderInput` objects and renders
them through :func:`~llm_dup_dev.review_html.render_results_html`, so the
"reconstruct a run and render it" logic lives once in the installable library
rather than being duplicated in every caller.

Everything is addressed by **explicit paths**. This module holds no knowledge
of dataset filenames, run-directory naming, or external URLs — a caller that
needs those supplies them (paths as arguments, links via
``external_url_builder``).

Two run shapes are supported:

``pair_results`` (preferred)
    A current run with a ``pair_results.json`` (the whole-``PairResult``
    artifact). Pass its path as ``pair_results_path``; each
    :class:`~llm_dup_dev.pipeline.runner.PairResult` — including its embedded
    narrative demographics — is restored directly.

``split`` (historical)
    An older run with only ``features.json`` and ``classifications.json``.
    Pass both; each ``PairResult`` is reconstructed from them, taking
    ``source_case_assessment`` from the features and narrative demographics
    from an optional per-report ``narrative_demographics`` bundle. The
    renderer consumes neither ``narrative_verdict`` nor ``ae_clinical_picture``,
    so a split run renders identically to a ``pair_results`` run.

When ``pair_results_path`` is given it wins, even if split files are also
present.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from llm_dup_dev.data.offline import (
    FrozenPairBundle,
    load_frozen_narrative_demographics,
)
from llm_dup_dev.models.classification import ClassificationResult
from llm_dup_dev.models.narrative_demographics import NarrativeDemographics
from llm_dup_dev.models.pair_features import PairFeatures
from llm_dup_dev.models.report import Report
from llm_dup_dev.models.report_pair import ReportPair
from llm_dup_dev.pipeline.export import (
    read_classifications,
    read_pair_features,
    read_pair_results,
)
from llm_dup_dev.pipeline.runner import PairResult

from . import ResultRenderInput, render_results_html


@dataclass
class SavedRunSummary:
    """A structured account of what a saved-run load/render did.

    Attributes
    ----------
    source : str
        ``"pair_results"`` if the run was loaded from ``pair_results.json``,
        ``"split"`` if reconstructed from ``features.json`` +
        ``classifications.json``.
    n_pairs : int
        Number of pairs considered (the length of the pairs artifact).
    n_loaded : int
        Number of ``ResultRenderInput`` objects successfully built.
    skipped : list[str]
        Human-readable reasons for each pair that could not be loaded.
    output_dir : Path or None
        Where HTML was written. Set by :func:`render_saved_run`; ``None`` after
        a bare :func:`load_saved_run`.
    n_rendered : int or None
        Number of pages rendered. Set by :func:`render_saved_run`; ``None``
        after a bare :func:`load_saved_run`.
    """

    source: str
    n_pairs: int
    n_loaded: int
    skipped: list[str] = field(default_factory=list)
    output_dir: Path | None = None
    n_rendered: int | None = None


def _load_json(path: str | Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_reports(path: str | Path) -> dict[str, Report]:
    """Load ``{report_id: Report}`` from a dict-keyed reports artifact."""
    return {
        str(rid): Report.model_validate(rdata)
        for rid, rdata in _load_json(path).items()
    }


def _load_pairs(path: str | Path) -> list[ReportPair]:
    """Load a list of ``ReportPair`` from a pairs artifact."""
    return [ReportPair.model_validate(row) for row in _load_json(path)]


def _load_bundles(path: str | Path | None) -> dict[str, FrozenPairBundle]:
    """Load ``{pair_id: FrozenPairBundle}``; ``{}`` when no path is given."""
    if path is None:
        return {}
    return {
        str(pid): FrozenPairBundle.model_validate(bdata)
        for pid, bdata in _load_json(path).items()
    }


def _load_annotations(path: str | Path | None) -> dict[str, dict]:
    """Load ``{pair_id: annotation}``; ``{}`` when no path is given.

    Accepts either a dict keyed by ``pair_id`` or a list of objects each
    carrying a ``pair_id`` field.
    """
    if path is None:
        return {}
    raw = _load_json(path)
    if isinstance(raw, list):
        out: dict[str, dict] = {}
        for row in raw:
            pid = row.get("pair_id")
            if pid is not None:
                out[str(pid)] = row
        return out
    return {str(pid): value for pid, value in raw.items()}


def _load_narrative_demographics(
    path: str | Path | None,
) -> dict[str, NarrativeDemographics]:
    """Load ``{report_id: NarrativeDemographics}``; ``{}`` when no path given."""
    if path is None:
        return {}
    return load_frozen_narrative_demographics(path).extractions


def load_saved_run(
    *,
    reports_path: str | Path,
    pairs_path: str | Path,
    pair_results_path: str | Path | None = None,
    features_path: str | Path | None = None,
    classifications_path: str | Path | None = None,
    annotations_path: str | Path | None = None,
    bundles_path: str | Path | None = None,
    narrative_demographics_path: str | Path | None = None,
    external_url_builder: Callable[[str, str], str | None] | None = None,
    external_url_label: str = "External link",
    include_debug: bool = True,
    strict: bool = False,
) -> tuple[list[ResultRenderInput], SavedRunSummary]:
    """Load a saved run into ``ResultRenderInput`` objects from explicit paths.

    Parameters
    ----------
    reports_path, pairs_path : str or Path
        Required. ``reports`` is a dict keyed by ``report_id``; ``pairs`` is a
        list of :class:`ReportPair`.
    pair_results_path : str or Path, optional
        Preferred source. When given (and it exists), the whole
        ``pair_results.json`` is used and the split files are ignored.
    features_path, classifications_path : str or Path, optional
        The split fallback, used when ``pair_results_path`` is not given. Both
        must be provided together.
    annotations_path, bundles_path, narrative_demographics_path : str or Path, optional
        Optional per-pair annotations, per-pair frozen frequency bundles, and a
        per-report narrative-demographics bundle. The demographics bundle is
        only consulted for the ``split`` source; a ``pair_results`` run already
        carries its demographics.
    external_url_builder : callable, optional
        ``(report_1_id, report_2_id) -> str | None``. Called per pair to build
        the external header link; ``None`` (or no builder) means no link. Keeps
        URL knowledge with the caller.
    external_url_label : str
        Label for the external link.
    include_debug : bool
        Passed through to each ``ResultRenderInput``.
    strict : bool
        When ``True``, a pair that cannot be loaded raises ``RuntimeError``
        instead of being skipped.

    Returns
    -------
    tuple[list[ResultRenderInput], SavedRunSummary]

    Raises
    ------
    ValueError
        If neither ``pair_results_path`` nor both split paths are provided.
    RuntimeError
        In ``strict`` mode, on the first unloadable pair.
    """
    use_pair_results = (
        pair_results_path is not None and Path(pair_results_path).exists()
    )

    pair_results: dict[str, PairResult] = {}
    features_by_pair: dict[str, PairFeatures] = {}
    classifications_by_pair: dict[str, ClassificationResult] = {}
    demographics_by_report: dict[str, NarrativeDemographics] = {}

    if use_pair_results:
        source = "pair_results"
        pair_results = read_pair_results(pair_results_path)  # type: ignore[arg-type]
    else:
        if features_path is None or classifications_path is None:
            raise ValueError(
                "Provide either pair_results_path, or both features_path and "
                "classifications_path."
            )
        source = "split"
        features_by_pair = read_pair_features(features_path)
        classifications_by_pair = read_classifications(classifications_path)
        demographics_by_report = _load_narrative_demographics(
            narrative_demographics_path
        )

    reports = _load_reports(reports_path)
    pairs = _load_pairs(pairs_path)
    bundles = _load_bundles(bundles_path)
    annotations = _load_annotations(annotations_path)

    items: list[ResultRenderInput] = []
    skipped: list[str] = []

    def skip(message: str) -> None:
        if strict:
            raise RuntimeError(message)
        skipped.append(message)
        print(f"WARNING: {message}", file=sys.stderr)

    for pair in pairs:
        pid = pair.pair_id
        r1_id = pair.report_1_id
        r2_id = pair.report_2_id

        if source == "pair_results":
            result = pair_results.get(pid)
            if result is None:
                skip(f"Skipping {pid}: no entry in pair_results.json")
                continue
        else:
            features = features_by_pair.get(pid)
            if features is None:
                skip(f"Skipping {pid}: missing features")
                continue
            classification = classifications_by_pair.get(pid)
            if classification is None:
                skip(f"Skipping {pid}: missing classification")
                continue

        report_1 = reports.get(r1_id)
        report_2 = reports.get(r2_id)
        if report_1 is None:
            skip(f"Skipping {pid}: missing report {r1_id}")
            continue
        if report_2 is None:
            skip(f"Skipping {pid}: missing report {r2_id}")
            continue

        if source == "split":
            result = PairResult(
                pair_id=pid,
                features=features,
                classification=classification,
                source_case_assessment=features.source_case_assessment,
                narrative_demographics_1=demographics_by_report.get(r1_id),
                narrative_demographics_2=demographics_by_report.get(r2_id),
            )

        external_url = (
            external_url_builder(r1_id, r2_id)
            if external_url_builder is not None
            else None
        )
        items.append(
            ResultRenderInput(
                result=result,
                report_1=report_1,
                report_2=report_2,
                external_url=external_url,
                external_url_label=external_url_label,
                frozen_bundle=bundles.get(pid),
                include_debug=include_debug,
                annotation=annotations.get(pid),
            )
        )

    summary = SavedRunSummary(
        source=source,
        n_pairs=len(pairs),
        n_loaded=len(items),
        skipped=skipped,
    )
    return items, summary


def render_saved_run(
    *,
    output_dir: str | Path,
    write_index: bool = True,
    **load_kwargs,
) -> SavedRunSummary:
    """Load a saved run and render it to HTML in one call.

    Forwards ``**load_kwargs`` to :func:`load_saved_run`, renders the resulting
    items with :func:`~llm_dup_dev.review_html.render_results_html`, and returns
    the summary with ``output_dir`` and ``n_rendered`` filled in.

    Parameters
    ----------
    output_dir : str or Path
        Directory to write HTML into (created if absent).
    write_index : bool
        Whether to generate an ``index.html`` linking to all pairs.
    **load_kwargs
        The keyword arguments of :func:`load_saved_run`.

    Returns
    -------
    SavedRunSummary
    """
    items, summary = load_saved_run(**load_kwargs)
    out = render_results_html(
        items, output_dir=Path(output_dir), write_index=write_index
    )
    summary.output_dir = out
    summary.n_rendered = len(items)
    return summary
