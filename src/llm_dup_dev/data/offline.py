"""Offline (frozen-validation) mode data implementations.

For evaluation batches that run without database access, the secure repo
preprocesses LLM- and database-dependent artifacts once and stores them
as frozen bundles. This module provides typed models for those artifacts
and the providers/loaders that serve them back into the pipeline.

Four artifacts are supported:

- ``FrozenPairBundle`` — per-pair frequency counts. Served by
  ``FrozenStatsProvider`` as a drop-in ``StatsProvider``.
- ``FrozenSourceCaseExtractions`` — per-report narrative source/case
  extractions (the output of the per-report LLM task). Passed directly
  to ``PipelineRunner`` via the ``frozen_source_case_extractions``
  constructor kwarg; the runner seeds its internal extraction cache
  from the bundle so covered reports skip live LLM calls.
- ``FrozenNarrativeDemographics`` — per-report narrative demographics
  extractions (age / sex / weight / height candidates with evidence
  quotes). Passed directly to ``PipelineRunner`` via the
  ``frozen_narrative_demographics`` constructor kwarg; the runner
  merges the candidates into each Report before downstream feature
  generation so the existing generous min-difference / set-intersection
  inconsistency checks include them automatically.
- ``ScreeningDetails`` — per-pair basis for the *screening* algorithm's
  classification (the algorithm that surfaced the candidate pair, not
  this pipeline). Consumed only by ``review_html`` for display, so
  reviewers can compare the adjudicator against the screening method.

Typical usage in the secure repo::

    from llm_dup_dev.data.offline import (
        FrozenPairBundle,
        FrozenScopeStats,
        FrozenSourceCaseExtractions,
        FrozenNarrativeDemographics,
        FrozenStatsProvider,
        save_frozen_source_case_extractions,
        save_frozen_narrative_demographics,
    )
    from llm_dup_dev.pipeline.runner import PipelineRunner

    bundles = {
        "R001__R002": FrozenPairBundle(
            pair_id="R001__R002",
            matching_drugs=["pregabalin", "sertraline"],
            matching_AEs=["somnolence"],
            global_stats=FrozenScopeStats(
                N_total=5000, N_drugs=200, N_AEs=300, N_drugs_AEs=40
            ),
            country_stats=FrozenScopeStats(
                N_total=1200, N_drugs=50, N_AEs=80, N_drugs_AEs=12
            ),
            country_year_stats=FrozenScopeStats(
                N_total=300, N_drugs=15, N_AEs=22, N_drugs_AEs=4
            ),
        ),
    }

    provider = FrozenStatsProvider(bundles)

    # Per-report extractions (one LLM call per unique report_id, once).
    source_case = FrozenSourceCaseExtractions(
        extractions={
            "R001": extract_source_case(llm, "R001", r1.narrative),
            "R002": extract_source_case(llm, "R002", r2.narrative),
        }
    )
    save_frozen_source_case_extractions(source_case, "source_case.json")

    demographics = FrozenNarrativeDemographics(
        extractions={
            "R001": extract_narrative_demographics(llm, "R001", r1.narrative),
            "R002": extract_narrative_demographics(llm, "R002", r2.narrative),
        }
    )
    save_frozen_narrative_demographics(demographics, "demographics.json")

    runner = PipelineRunner(
        report_loader=loader,
        stats_provider=provider,
        frozen_source_case_extractions=source_case,
        frozen_narrative_demographics=demographics,
    )

``FrozenStatsProvider`` implements both ``StatsProvider`` and
``PairContextSink``. ``PipelineRunner`` calls ``set_current_pair`` before
each ``compute_stats``, so no API change is needed on the runner side.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence, TypeVar

from pydantic import BaseModel, ValidationError

_BundleT = TypeVar("_BundleT", bound=BaseModel)


def _save_frozen_bundle(bundle: BaseModel, path: str | Path) -> None:
    """Serialize a frozen bundle to a JSON file with ``indent=2``.

    Shared by the typed ``save_frozen_*`` wrappers below so artifacts
    stay readable in code review and diff tools.
    """
    Path(path).write_text(bundle.model_dump_json(indent=2))


def _load_frozen_bundle(
    path: str | Path, model_cls: type[_BundleT]
) -> _BundleT:
    """Load and validate a frozen bundle from a JSON file.

    Raises Pydantic ``ValidationError`` on schema drift — surfaces
    incompatibilities between frozen artifacts and the current code
    loudly rather than silently.
    """
    return model_cls.model_validate_json(Path(path).read_text())

from llm_dup_dev.feature_generation.probability import FrequencyStats
from llm_dup_dev.models.narrative_demographics import NarrativeDemographics
from llm_dup_dev.models.narrative_source_case import NarrativeSourceCase


class FrozenScopeStats(BaseModel):
    """Precomputed frequency counts for a single reporting scope.

    Stores the same four counts as ``FrequencyStats``, scoped to a
    specific reporting context (global / country / country+year).

    Attributes
    ----------
    N_total : int
        Total reports in this reporting context.
    N_drugs : int
        Reports containing all matching drugs within this context.
    N_AEs : int
        Reports containing all matching AEs within this context.
    N_drugs_AEs : int
        Reports containing both all matching drugs and all matching AEs.
    """

    N_total: int
    N_drugs: int
    N_AEs: int
    N_drugs_AEs: int


class FrozenPairBundle(BaseModel):
    """Precomputed pair-level frequency artifact.

    One bundle per pair. Contains frequency counts for every applicable
    reporting scope so the offline provider can serve the right scope
    without querying a database.

    Scope rules (mirror the pipeline's ``determine_reporting_context``):

    - ``global_stats``      — always present; global fallback
    - ``country_stats``     — present when the pair's reports share a country
    - ``country_year_stats``— present when the reports share both country and year

    ``matching_drugs`` and ``matching_AEs`` are stored so the offline
    provider can verify they match what the pipeline recomputes at eval
    time. Both lists must be in **canonical sorted order** (the same
    case-insensitive lexicographic order the pipeline uses internally).

    Attributes
    ----------
    pair_id : str
        Identifier matching the ``ReportPair.pair_id`` used at eval time.
    matching_drugs : list[str]
        Canonical sorted list of shared drug substances.
    matching_AEs : list[str]
        Canonical sorted list of shared AE preferred terms.
    global_stats : FrozenScopeStats
        Counts for the global context. Always required.
    country_stats : FrozenScopeStats or None
        Counts for the country context. Set when reports share a country.
    country_year_stats : FrozenScopeStats or None
        Counts for the country+year context. Set when reports share both.
    """

    pair_id: str
    matching_drugs: list[str]
    matching_AEs: list[str]
    global_stats: FrozenScopeStats
    country_stats: FrozenScopeStats | None = None
    country_year_stats: FrozenScopeStats | None = None


class FrozenStatsProvider:
    """StatsProvider backed by precomputed per-pair frequency bundles.

    Designed for offline/frozen-validation evaluation where database
    access is unavailable. The secure repo's preprocessing step computes
    frequency counts for every pair and stores them as
    ``FrozenPairBundle`` objects.

    This class implements both the ``StatsProvider`` protocol and the
    ``PairContextSink`` protocol. ``PipelineRunner`` detects
    ``PairContextSink`` and calls ``set_current_pair`` before each
    ``compute_stats``, so the provider knows which bundle to look up.

    Validation
    ----------
    At ``compute_stats`` time the provider checks:

    1. ``set_current_pair`` was called (``RuntimeError`` if not).
    2. A bundle exists for the current ``pair_id`` (``KeyError`` if not).
    3. The requested reporting scope is stored in the bundle
       (``KeyError`` if not).
    4. The pipeline's recomputed ``matching_drugs`` / ``matching_AEs``
       match the frozen values (``ValueError`` if not).

    These checks prevent silent drift between frozen artifacts and the
    pipeline's matching logic.

    Parameters
    ----------
    bundles : dict[str, FrozenPairBundle]
        Mapping of ``pair_id`` → ``FrozenPairBundle``.
    """

    def __init__(self, bundles: dict[str, FrozenPairBundle]) -> None:
        self._bundles: dict[str, FrozenPairBundle] = dict(bundles)
        self._current_pair_id: str | None = None

    # ------------------------------------------------------------------
    # PairContextSink protocol
    # ------------------------------------------------------------------

    def set_current_pair(self, pair_id: str) -> None:
        """Record the pair being processed.

        Called by ``PipelineRunner`` before ``compute_stats``. Must be
        called once per pair; the provider resets automatically on the
        next call.
        """
        self._current_pair_id = pair_id

    # ------------------------------------------------------------------
    # StatsProvider protocol
    # ------------------------------------------------------------------

    def compute_stats(
        self,
        matching_drugs: Sequence[str],
        matching_AEs: Sequence[str],
        country: str | None,
        year: int | None,
    ) -> FrequencyStats:
        """Return precomputed counts for the current pair's reporting scope.

        Parameters
        ----------
        matching_drugs : Sequence[str]
            Matching drug substances as computed by the pipeline
            (canonical sorted order).
        matching_AEs : Sequence[str]
            Matching AE preferred terms as computed by the pipeline
            (canonical sorted order).
        country : str or None
            Reporting country from the pipeline's reporting context.
        year : int or None
            Reporting year from the pipeline's reporting context.

        Returns
        -------
        FrequencyStats

        Raises
        ------
        RuntimeError
            If ``set_current_pair`` was not called before this method.
        KeyError
            If no bundle exists for the current ``pair_id``, or the
            requested scope is absent from the bundle.
        ValueError
            If the pipeline's recomputed ``matching_drugs`` or
            ``matching_AEs`` disagree with the frozen bundle values.
        """
        if self._current_pair_id is None:
            raise RuntimeError(
                "FrozenStatsProvider.set_current_pair() must be called before "
                "compute_stats(). Ensure PipelineRunner is used and that this "
                "provider is passed as stats_provider — the runner calls "
                "set_current_pair() automatically when it detects PairContextSink."
            )

        bundle = self._bundles.get(self._current_pair_id)
        if bundle is None:
            raise KeyError(
                f"No frozen bundle found for pair_id {self._current_pair_id!r}. "
                "Ensure the preprocessing step included this pair."
            )

        # Verify pipeline-recomputed matches agree with the frozen artifact.
        # A mismatch means the preprocessing and the current pipeline version
        # use different matching logic — fail loudly rather than produce
        # silently wrong probability estimates.
        recomputed_drugs = list(matching_drugs)
        recomputed_aes = list(matching_AEs)
        if recomputed_drugs != bundle.matching_drugs:
            raise ValueError(
                f"matching_drugs mismatch for pair {self._current_pair_id!r}: "
                f"pipeline computed {recomputed_drugs!r}, "
                f"bundle stores {bundle.matching_drugs!r}. "
                "Regenerate frozen bundles or check for pipeline version drift."
            )
        if recomputed_aes != bundle.matching_AEs:
            raise ValueError(
                f"matching_AEs mismatch for pair {self._current_pair_id!r}: "
                f"pipeline computed {recomputed_aes!r}, "
                f"bundle stores {bundle.matching_AEs!r}. "
                "Regenerate frozen bundles or check for pipeline version drift."
            )

        scope = self._select_scope(bundle, country, year)

        # Broader-context counts feed the first-level-with-evidence
        # backoff cascade in ``compute_probability`` for drug-set /
        # AE-set marginals. Only populate levels that are *strictly
        # broader* than the primary scope so that the same data is not
        # consulted twice:
        #   - primary = country-year → country and global both broader
        #   - primary = country      → only global is broader
        #   - primary = global       → no broader level
        is_country_year_primary = country is not None and year is not None
        is_country_primary = country is not None and year is None
        country_stats = (
            bundle.country_stats if is_country_year_primary else None
        )
        global_stats = (
            bundle.global_stats
            if (is_country_year_primary or is_country_primary)
            else None
        )
        return FrequencyStats(
            N_total=scope.N_total,
            N_drugs=scope.N_drugs,
            N_AEs=scope.N_AEs,
            N_drugs_AEs=scope.N_drugs_AEs,
            N_total_country=(
                country_stats.N_total if country_stats is not None else None
            ),
            N_drugs_country=(
                country_stats.N_drugs if country_stats is not None else None
            ),
            N_AEs_country=(
                country_stats.N_AEs if country_stats is not None else None
            ),
            N_total_global=(
                global_stats.N_total if global_stats is not None else None
            ),
            N_drugs_global=(
                global_stats.N_drugs if global_stats is not None else None
            ),
            N_AEs_global=(
                global_stats.N_AEs if global_stats is not None else None
            ),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _select_scope(
        self,
        bundle: FrozenPairBundle,
        country: str | None,
        year: int | None,
    ) -> FrozenScopeStats:
        """Select the bundle scope that matches the pipeline's reporting context.

        Mirrors ``determine_reporting_context`` scope rules:
        - country + year  → ``country_year_stats``
        - country only    → ``country_stats``
        - neither         → ``global_stats``
        """
        if country is not None and year is not None:
            if bundle.country_year_stats is None:
                raise KeyError(
                    f"country_year scope requested for pair "
                    f"{self._current_pair_id!r} "
                    f"(country={country!r}, year={year}), but "
                    "country_year_stats is not stored in the frozen bundle. "
                    "Check that preprocessing used the same scope rules as "
                    "the pipeline."
                )
            return bundle.country_year_stats

        if country is not None:
            if bundle.country_stats is None:
                raise KeyError(
                    f"country scope requested for pair "
                    f"{self._current_pair_id!r} "
                    f"(country={country!r}), but country_stats is not stored "
                    "in the frozen bundle. Check that preprocessing used the "
                    "same scope rules as the pipeline."
                )
            return bundle.country_stats

        return bundle.global_stats


# ------------------------------------------------------------------
# Frozen narrative source/case extractions
# ------------------------------------------------------------------


class FrozenSourceCaseExtractions(BaseModel):
    """Precomputed per-report narrative source/case extractions.

    Produced once by the secure repo (one LLM call per unique report)
    and serialized as a single JSON artifact. Passed to
    ``PipelineRunner`` to skip live LLM calls during offline
    evaluation.

    Partial bundles are allowed: ``report_id`` keys present in the
    bundle are served from it; missing ``report_id`` keys fall back to
    live extraction (or ``None`` if no LLM client is configured on the
    runner). This is intentionally more lenient than
    ``FrozenStatsProvider`` because source/case is advisory — only the
    source-context evidence in the decision logic consumes it, and that
    evidence is designed to be skippable.

    Attributes
    ----------
    extractions : dict[str, NarrativeSourceCase]
        Mapping of ``report_id`` → per-report extraction.
    """

    extractions: dict[str, NarrativeSourceCase]


def save_frozen_source_case_extractions(
    bundle: FrozenSourceCaseExtractions, path: str | Path
) -> None:
    """Serialize a source/case bundle to a JSON file."""
    _save_frozen_bundle(bundle, path)


def load_frozen_source_case_extractions(
    path: str | Path,
) -> FrozenSourceCaseExtractions:
    """Load a source/case bundle from a JSON file.

    Raises Pydantic ``ValidationError`` if the file contents do not
    match the current ``FrozenSourceCaseExtractions`` /
    ``NarrativeSourceCase`` schemas.
    """
    return _load_frozen_bundle(path, FrozenSourceCaseExtractions)


# ------------------------------------------------------------------
# Frozen narrative demographics extractions
# ------------------------------------------------------------------


class FrozenNarrativeDemographics(BaseModel):
    """Precomputed per-report narrative demographics extractions.

    Produced once by the secure repo (one LLM call per unique report)
    and serialized as a single JSON artifact. Passed to
    ``PipelineRunner`` to skip live LLM calls during offline
    evaluation. The runner merges each covered extraction into the
    corresponding ``Report`` via ``merge_narrative_demographics``
    before downstream feature generation, so the existing generous
    min-difference / set-intersection inconsistency checks include the
    narrative candidates automatically.

    Partial bundles are allowed: ``report_id`` keys present in the
    bundle are served from it; missing ``report_id`` keys fall back to
    live extraction (or to an empty ``NarrativeDemographics()`` when no
    LLM client is configured on the runner). Same graceful-partial
    semantics as ``FrozenSourceCaseExtractions``.

    Attributes
    ----------
    extractions : dict[str, NarrativeDemographics]
        Mapping of ``report_id`` → per-report extraction.
    """

    extractions: dict[str, NarrativeDemographics]


def save_frozen_narrative_demographics(
    bundle: FrozenNarrativeDemographics, path: str | Path
) -> None:
    """Serialize a demographics bundle to a JSON file."""
    _save_frozen_bundle(bundle, path)


def load_frozen_narrative_demographics(
    path: str | Path,
) -> FrozenNarrativeDemographics:
    """Load a demographics bundle from a JSON file.

    Raises Pydantic ``ValidationError`` if the file contents do not
    match the current ``FrozenNarrativeDemographics`` /
    ``NarrativeDemographics`` schemas.
    """
    return _load_frozen_bundle(path, FrozenNarrativeDemographics)


# ------------------------------------------------------------------
# Screening algorithm details
# ------------------------------------------------------------------


ScreeningValue = str | int | float | bool | None
ScreeningEntry = dict[str, ScreeningValue | list[ScreeningValue]]


class ScreeningDetails(BaseModel):
    """Per-pair basis for a *screening* algorithm's classification.

    The screening algorithm is whatever surfaced the pair as a duplicate
    candidate in the first place — not this pipeline. Reviewers comparing
    the adjudicator's verdict against the screening method need to see
    what the screening method actually computed, so ``review_html``
    renders each entry verbatim in its own section on the pair page.

    Screening algorithms vary, so the per-pair payload is deliberately
    untyped at the feature level: any set of ``feature -> value`` pairs
    is accepted, and key insertion order is the display order (the
    algorithm decides what to show first, not this repo). Values must be
    scalars or flat lists of scalars; a nested object is rejected,
    because there is no sensible way to render one in a two-column
    table.

    Attributes
    ----------
    algorithm : str, optional
        Name of the screening algorithm (e.g. ``"vigiMatch"``). Shown in
        the section heading when present.
    entries : dict[str, ScreeningEntry]
        Mapping of ``pair_id`` → that pair's feature/value payload.
        Partial coverage is fine: pairs absent from the mapping render
        with no screening section at all.
    """

    algorithm: str | None = None
    entries: dict[str, ScreeningEntry]


def save_screening_details(details: ScreeningDetails, path: str | Path) -> None:
    """Serialize screening details to a JSON file."""
    _save_frozen_bundle(details, path)


def load_screening_details(path: str | Path) -> ScreeningDetails:
    """Load screening details from a JSON file.

    Accepts the wrapper shape ``{"algorithm": ..., "entries": {pair_id:
    {...}}}`` or the plain mapping ``{pair_id: {...}}``; the plain form is
    normalised into the wrapper (leaving ``algorithm`` unset) before
    validation, so the model does the per-entry parsing rather than a
    bespoke loader. Same trick ``review_html.saved_run`` uses for the
    plain narrative-demographics shape.

    A top-level ``entries`` key is what distinguishes the two shapes; it
    can never be a pair id, since canonical pair ids always contain the
    ``__`` separator.

    Raises
    ------
    ValueError
        If the file is not valid JSON of either shape, or a feature value
        is not a scalar or flat list of scalars.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
        payload = raw
    else:
        payload = {"entries": raw}
    try:
        return ScreeningDetails.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(
            f"Invalid screening-details artifact {path}: {exc}"
        ) from exc
