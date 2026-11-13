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
    renderer does not consume ``ae_clinical_picture``, so a split run
    renders identically to a ``pair_results`` run.

When ``pair_results_path`` is given it wins, even if split files are also
present.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from llm_dup_dev.data.offline import (
    FrozenNarrativeDemographics,
    FrozenPairBundle,
    ScreeningDetails,
    index_by_id,
    load_reports,
    load_screening_details,
)
from llm_dup_dev.models.classification import ClassificationResult
from llm_dup_dev.models.narrative_demographics import NarrativeDemographics
from llm_dup_dev.models.pair_features import PairFeatures
from llm_dup_dev.models.pair_identity import (
    canonical_report_ids,
    split_pair_id,
)
from llm_dup_dev.models.report_pair import (
    ALLOW_NONCANONICAL_CONTEXT_KEY,
    ReportPair,
)
from llm_dup_dev.pipeline.export import (
    read_classifications,
    read_pair_features,
    read_pair_results,
)
from llm_dup_dev.pipeline.runner import PairResult

from . import ResultRenderInput, render_results_html


class SkipReason(str, Enum):
    """Stable, machine-comparable reasons a pair was skipped during load.

    Subclasses ``str`` so a :class:`SavedRunSkip` round-trips through JSON and
    compares equal to its literal value, while callers can match members
    (``skip.reason is SkipReason.MISSING_REPORT_1``) without parsing strings.
    """

    MISSING_RESULT = "missing_result"
    MISSING_FEATURES = "missing_features"
    MISSING_CLASSIFICATION = "missing_classification"
    MISSING_REPORT_1 = "missing_report_1"
    MISSING_REPORT_2 = "missing_report_2"


@dataclass(frozen=True)
class SavedRunSkip:
    """One pair that could not be loaded, as a typed record.

    Attributes
    ----------
    pair_id : str
        The pair that was skipped.
    reason : SkipReason
        A stable, enumerated reason (compare against :class:`SkipReason`).
    detail : str
        Human-readable specifics (e.g. the missing report id).
    """

    pair_id: str
    reason: SkipReason
    detail: str


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
    skipped : list[SavedRunSkip]
        A typed record for each pair that could not be loaded. Empty in
        ``strict`` mode (the first skip raises instead).
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
    skipped: list[SavedRunSkip] = field(default_factory=list)
    output_dir: Path | None = None
    n_rendered: int | None = None


def _load_json(path: str | Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ``index_by_id`` and ``load_reports`` moved to ``data.offline`` so the review
# renderer, the characterization CLI and any secure-side notebook share one
# definition of "read an id-keyed artifact" rather than three.


def _load_pairs(
    path: str | Path, *, allow_noncanonical: bool = False
) -> list[ReportPair]:
    """Load a list of ``ReportPair`` from a pairs artifact.

    ``allow_noncanonical`` relaxes the canonical-identity check so an artifact
    written before pair ids were canonicalised can still be rendered. Blank
    report ids and self-pairs remain rejected.
    """
    raw = _load_json(path)
    if not isinstance(raw, list):
        raise ValueError(
            f"Invalid pairs artifact {path}: top-level JSON must be a list, "
            f"got {type(raw).__name__}."
        )
    context = (
        {ALLOW_NONCANONICAL_CONTEXT_KEY: True} if allow_noncanonical else None
    )
    return [ReportPair.model_validate(row, context=context) for row in raw]


def _check_artifact_keys_canonical(
    keys: Iterable[str], *, artifact: str, path: str | Path | None
) -> None:
    """Raise if a pair-keyed artifact still uses pre-canonical pair ids.

    ``PairFeatures`` and ``ClassificationResult`` carry no report ids, so a
    stale artifact keyed ``B__A`` cannot be detected from its contents — it
    would simply fail to join and silently lose coverage. Its *keys* can be
    checked, though: a key that splits into two report ids which are not in
    canonical order was written before canonicalisation.

    Keys that do not split into exactly two ids are left alone. ``pair_id`` on
    these types is free-form by design (a caller may render a pair under any
    label), and only the canonical form carries the ordering promise.
    """
    stale: list[tuple[str, str]] = []
    for key in keys:
        try:
            first, second = split_pair_id(key)
            lower, higher = canonical_report_ids(first, second)
        except ValueError:
            continue
        if (first, second) != (lower, higher):
            stale.append((key, f"{lower}__{higher}"))
    if stale:
        found, expected = stale[0]
        raise ValueError(
            f"Invalid {artifact} artifact {path}: {len(stale)} key(s) are not "
            f"canonical pair ids, e.g. {found!r} (expected {expected!r}). This "
            f"artifact predates canonical pair identity and will not join "
            f"against canonical pairs. Regenerate it — re-keying it here would "
            f"be guessing which stored record belongs to which pair."
        )


def _load_bundles(path: str | Path | None) -> dict[str, FrozenPairBundle]:
    """Load ``{pair_id: FrozenPairBundle}``; ``{}`` when no path is given.

    Accepts a list of bundles (the secure runner's shape) or a mapping keyed by
    ``pair_id`` (the review CLI's shape).
    """
    if path is None:
        return {}
    return index_by_id(
        _load_json(path),
        id_attr="pair_id",
        model_cls=FrozenPairBundle,
        artifact="frozen bundles",
        path=path,
    )


def _load_annotations(path: str | Path | None) -> dict[str, dict]:
    """Load ``{pair_id: annotation}``; ``{}`` when no path is given.

    Accepts either a list of objects each carrying a ``pair_id`` or a mapping
    keyed by ``pair_id``. Validated hard: list items must be objects with a
    ``pair_id``; a mapping value embedding a ``pair_id`` must match its key;
    duplicate pair ids are rejected (never silently overwritten); any other
    top-level shape is rejected.
    """
    if path is None:
        return {}
    raw = _load_json(path)
    prefix = f"Invalid annotations artifact {path}"
    out: dict[str, dict] = {}

    def _add(pid: str, value: dict) -> None:
        if pid in out:
            raise ValueError(f"{prefix}: duplicate pair_id={pid!r}.")
        out[pid] = value

    if isinstance(raw, list):
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ValueError(
                    f"{prefix}: item {i} must be an object, got "
                    f"{type(item).__name__}."
                )
            pid = item.get("pair_id")
            if pid is None:
                raise ValueError(f"{prefix}: item {i} is missing 'pair_id'.")
            _add(str(pid), item)
    elif isinstance(raw, dict):
        for key, value in raw.items():
            if not isinstance(value, dict):
                raise ValueError(
                    f"{prefix}: value for {key!r} must be an object, got "
                    f"{type(value).__name__}."
                )
            embedded = value.get("pair_id")
            if embedded is not None and str(embedded) != str(key):
                raise ValueError(
                    f"{prefix}: mapping key {key!r} does not match embedded "
                    f"pair_id={embedded!r}."
                )
            _add(str(key), value)
    else:
        raise ValueError(
            f"{prefix}: top-level JSON must be a list or an object, got "
            f"{type(raw).__name__}."
        )
    return out


def _load_narrative_demographics(
    path: str | Path | None,
) -> dict[str, NarrativeDemographics]:
    """Load ``{report_id: NarrativeDemographics}``; ``{}`` when no path given.

    Accepts the wrapper shape ``{"extractions": {report_id: {...}}}`` or the
    plain mapping ``{report_id: {...}}``; the plain form is normalised into the
    wrapper before validation, so the official
    :class:`~llm_dup_dev.data.offline.FrozenNarrativeDemographics` model does the
    per-item parsing rather than a bespoke loader.
    """
    if path is None:
        return {}
    raw = _load_json(path)
    if isinstance(raw, dict) and "extractions" in raw:
        inner = raw["extractions"]
    else:
        inner = raw
    try:
        bundle = FrozenNarrativeDemographics.model_validate(
            {"extractions": inner}
        )
    except ValidationError as exc:
        raise ValueError(
            f"Invalid narrative-demographics artifact {path}: {exc}"
        ) from exc
    return bundle.extractions


def _load_screening(path: str | Path | None) -> ScreeningDetails:
    """Load screening details; an empty ``ScreeningDetails`` when no path.

    Delegates to :func:`~llm_dup_dev.data.offline.load_screening_details`,
    which accepts the wrapper and bare-mapping shapes alike.
    """
    if path is None:
        return ScreeningDetails(entries={})
    return load_screening_details(path)


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
    screening_path: str | Path | None = None,
    external_url_builder: Callable[[str, str], str | None] | None = None,
    external_url_label: str = "External link",
    include_debug: bool = True,
    strict: bool = True,
    allow_noncanonical_pairs: bool = False,
) -> tuple[list[ResultRenderInput], SavedRunSummary]:
    """Load a saved run into ``ResultRenderInput`` objects from explicit paths.

    Source selection is by precedence, not existence:

    * ``pair_results_path is not None`` → that artifact is used; a missing file
      raises ``FileNotFoundError`` and a malformed one raises its parse/
      validation error. There is no silent fall-back to the split files.
    * ``pair_results_path is None`` → the split source is used, which requires
      **both** ``features_path`` and ``classifications_path``.

    Parameters
    ----------
    reports_path, pairs_path : str or Path
        Required. ``reports`` is either a list of report objects or a mapping
        keyed by ``report_id``; ``pairs`` is a list of :class:`ReportPair`.
    pair_results_path : str or Path, optional
        The preferred source. When given it is mandatory (see precedence above).
    features_path, classifications_path : str or Path, optional
        The split source, used only when ``pair_results_path`` is ``None``. Both
        must be provided together.
    annotations_path, bundles_path, narrative_demographics_path : str or Path, optional
        Optional per-pair annotations, per-pair frozen frequency bundles (list
        or mapping shaped), and a per-report narrative-demographics bundle
        (wrapper or plain shaped). The demographics bundle is only consulted for
        the ``split`` source; a ``pair_results`` run already carries its
        demographics.
    screening_path : str or Path, optional
        A :class:`~llm_dup_dev.data.offline.ScreeningDetails` artifact (wrapper
        or bare pair-keyed shape) describing the basis for the *screening*
        algorithm's classification. Pairs it does not cover render without a
        screening section.
    external_url_builder : callable, optional
        ``(report_1_id, report_2_id) -> str | None``. Called per pair to build
        the external header link; ``None`` (or no builder) means no link, and a
        builder that raises is caught (warning emitted, link omitted) — it never
        drops the pair. Keeps URL knowledge with the caller.
    external_url_label : str
        Label for the external link.
    include_debug : bool
        Passed through to each ``ResultRenderInput``.
    strict : bool
        The reusable-library default is ``True``: the first pair that cannot be
        loaded raises ``RuntimeError``. Pass ``strict=False`` for warn-and-skip
        (each skipped pair is recorded in ``SavedRunSummary.skipped``). A failing
        ``external_url_builder`` is *not* governed by ``strict`` — it is always
        tolerated by rendering the pair without a link.
    allow_noncanonical_pairs : bool
        Escape hatch for rendering a run saved **before** pair ids were
        canonicalised (see :mod:`llm_dup_dev.models.pair_identity`). It relaxes
        the canonical-identity check on the pairs artifact and the matching
        check on the pair-keyed artifacts' keys, so an old run can be viewed
        without regenerating it. Rendering is read-only presentation; anything
        that feeds an evaluation must be regenerated instead. Blank report ids
        and self-pairs stay rejected either way.

    Returns
    -------
    tuple[list[ResultRenderInput], SavedRunSummary]

    Raises
    ------
    ValueError
        If ``pair_results_path`` is ``None`` and both split paths are not given,
        or if an artifact is keyed by pre-canonical pair ids and
        ``allow_noncanonical_pairs`` is not set.
    FileNotFoundError
        If ``pair_results_path`` is given but the file does not exist.
    RuntimeError
        In ``strict`` mode, on the first unloadable pair.
    """
    pair_results: dict[str, PairResult] = {}
    features_by_pair: dict[str, PairFeatures] = {}
    classifications_by_pair: dict[str, ClassificationResult] = {}
    demographics_by_report: dict[str, NarrativeDemographics] = {}

    if pair_results_path is not None:
        source = "pair_results"
        if not Path(pair_results_path).exists():
            raise FileNotFoundError(
                f"pair_results_path does not exist: {pair_results_path}"
            )
        # A malformed artifact raises its parse/validation error here.
        pair_results = read_pair_results(pair_results_path)
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

    reports = load_reports(reports_path)
    pairs = _load_pairs(pairs_path, allow_noncanonical=allow_noncanonical_pairs)
    bundles = _load_bundles(bundles_path)
    annotations = _load_annotations(annotations_path)
    screening = _load_screening(screening_path)

    if not allow_noncanonical_pairs:
        # These artifacts are keyed by pair_id and carry no report ids, so a
        # stale key would silently fail to join rather than raise.
        if source == "pair_results":
            _check_artifact_keys_canonical(
                pair_results, artifact="pair_results", path=pair_results_path
            )
        else:
            _check_artifact_keys_canonical(
                features_by_pair, artifact="features", path=features_path
            )
            _check_artifact_keys_canonical(
                classifications_by_pair,
                artifact="classifications",
                path=classifications_path,
            )
        if bundles:
            _check_artifact_keys_canonical(
                bundles, artifact="bundles", path=bundles_path
            )
        if screening.entries:
            _check_artifact_keys_canonical(
                screening.entries, artifact="screening", path=screening_path
            )

    items: list[ResultRenderInput] = []
    skipped: list[SavedRunSkip] = []

    def skip(pair_id: str, reason: SkipReason, detail: str) -> None:
        if strict:
            raise RuntimeError(f"{reason.value}: {pair_id} — {detail}")
        skipped.append(SavedRunSkip(pair_id=pair_id, reason=reason, detail=detail))
        print(f"WARNING: skipping {pair_id} ({reason.value}): {detail}",
              file=sys.stderr)

    for pair in pairs:
        pid = pair.pair_id
        r1_id = pair.report_1_id
        r2_id = pair.report_2_id

        if source == "pair_results":
            result = pair_results.get(pid)
            if result is None:
                skip(pid, SkipReason.MISSING_RESULT,
                     "no entry in pair_results artifact")
                continue
        else:
            features = features_by_pair.get(pid)
            if features is None:
                skip(pid, SkipReason.MISSING_FEATURES,
                     "no entry in features artifact")
                continue
            classification = classifications_by_pair.get(pid)
            if classification is None:
                skip(pid, SkipReason.MISSING_CLASSIFICATION,
                     "no entry in classifications artifact")
                continue
            result = PairResult(
                pair_id=pid,
                features=features,
                classification=classification,
                source_case_assessment=features.source_case_assessment,
                narrative_demographics_1=demographics_by_report.get(r1_id),
                narrative_demographics_2=demographics_by_report.get(r2_id),
            )

        report_1 = reports.get(r1_id)
        report_2 = reports.get(r2_id)
        if report_1 is None:
            skip(pid, SkipReason.MISSING_REPORT_1, f"missing report {r1_id}")
            continue
        if report_2 is None:
            skip(pid, SkipReason.MISSING_REPORT_2, f"missing report {r2_id}")
            continue

        external_url: str | None = None
        if external_url_builder is not None:
            try:
                external_url = external_url_builder(r1_id, r2_id)
            except Exception as exc:  # caller-supplied callback; never fatal
                print(
                    f"WARNING: external_url_builder failed for {pid} "
                    f"({r1_id}, {r2_id}); rendering without a link: {exc}",
                    file=sys.stderr,
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
                screening=screening.entries.get(pid),
                screening_algorithm=screening.algorithm,
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
