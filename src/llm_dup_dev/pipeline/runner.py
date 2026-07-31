"""End-to-end pipeline runner.

Wires together data loading, feature matching, reporting context
determination, stats retrieval, feature generation, and classification
into a single processing flow.

Usage::

    from llm_dup_dev.pipeline.runner import PipelineRunner

    runner = PipelineRunner(
        report_loader=loader,
        stats_provider=stats_provider,
    )

    # Single pair
    result = runner.run_pair(pair)

    # Batch
    results = runner.run_batch(pairs)

With LLM-backed assessments::

    from llm_dup_dev.pipeline.runner import PipelineRunner

    runner = PipelineRunner(
        report_loader=loader,
        stats_provider=stats_provider,
        llm_client=my_azure_client,
    )

    # Batch — narratives are assessed automatically via the LLM
    results = runner.run_batch(pairs)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal, Sequence

from llm_dup_dev.classifier.config import DecisionThresholds, PipelineConfig
from llm_dup_dev.classifier.decision_logic import classify
from llm_dup_dev.data.offline import (
    FrozenNarrativeDemographics,
    FrozenSourceCaseExtractions,
)
from llm_dup_dev.data.protocols import PairContextSink, ReportLoader, StatsProvider
from llm_dup_dev.feature_generation.ae_clinical_picture_rules import (
    assess_ae_clinical_picture_rule_based,
)
from llm_dup_dev.feature_generation.matching import compute_matches
from llm_dup_dev.feature_generation.narrative_demographics import (
    merge_narrative_demographics,
)
from llm_dup_dev.feature_generation.pipeline import generate_pair_features
from llm_dup_dev.feature_generation.reporting_context import (
    determine_reporting_context,
)
from llm_dup_dev.feature_generation.source_case_comparison import compare_source_case
from llm_dup_dev.llm.errors import LLMConfigurationError
from llm_dup_dev.llm.protocols import LLMClient
from llm_dup_dev.llm.tasks.ae_clinical_picture import assess_ae_clinical_picture
from llm_dup_dev.llm.tasks.narrative_demographics import (
    extract_narrative_demographics,
)
from llm_dup_dev.llm.tasks.narrative_source_case import extract_source_case
from llm_dup_dev.models.classification import ClassificationResult
from llm_dup_dev.models.enums import (
    AEClinicalPictureStatus,
    ReportingContextLevel,
)
from llm_dup_dev.models.narrative_demographics import NarrativeDemographics
from llm_dup_dev.models.narrative_source_case import (
    NarrativeSourceCase,
    SourceCaseAssessment,
)
from llm_dup_dev.models.pair_features import (
    AEClinicalPictureResult,
    PairFeatures,
)
from llm_dup_dev.models.report import Report
from llm_dup_dev.models.report_pair import ReportPair

logger = logging.getLogger(__name__)


@dataclass
class PairResult:
    """Complete result for a single pair: features + classification.

    Attributes
    ----------
    pair_id : str
        Identifier of the report pair.
    features : PairFeatures
        Computed feature set (probability, inconsistencies, etc.).
    classification : ClassificationResult
        Final classification outcome.
    ae_clinical_picture : AEClinicalPictureResult or None
        Full AE clinical-picture assessment result (status, counts and
        unified findings) when an LLM client was used.  ``None`` when
        no LLM client was configured (the merged ``PairFeatures`` will
        carry status NOT_ASSESSED with reason ``"no_llm_client"``).
    narrative_demographics_1, narrative_demographics_2 : NarrativeDemographics or None
        The per-report narrative-demographics extractions used during
        feature generation, carried through so downstream consumers
        (notably the HTML review renderer) can re-apply
        ``merge_narrative_demographics`` and surface the extracted
        candidates in the demographics table.
    """

    pair_id: str
    features: PairFeatures
    classification: ClassificationResult
    ae_clinical_picture: AEClinicalPictureResult | None = None
    source_case_assessment: SourceCaseAssessment | None = None
    narrative_demographics_1: NarrativeDemographics | None = None
    narrative_demographics_2: NarrativeDemographics | None = None

    @property
    def final_label(self) -> str:
        """Convenience accessor for the eval-set label."""
        return self.classification.final_label


@dataclass
class BatchResult:
    """Results for a batch of pairs."""

    results: list[PairResult] = field(default_factory=list)
    errors: list[tuple[str, Exception]] = field(default_factory=list)

    @property
    def n_processed(self) -> int:
        return len(self.results)

    @property
    def n_errors(self) -> int:
        return len(self.errors)

    def labels(self) -> dict[str, str]:
        """Return {pair_id: final_label} mapping for eval integration."""
        return {r.pair_id: r.final_label for r in self.results}


class PipelineRunner:
    """End-to-end pipeline: load data → generate features → classify.

    Wires together:
        1. Report loading (via ReportLoader protocol)
        2. Feature matching and reporting context
        3. Stats retrieval (via StatsProvider protocol)
        4. LLM assessment (AE clinical-picture inconsistency via LLMClient)
        5. Feature generation (probability, inconsistencies, anchor)
        6. Decision logic classification

    Parameters
    ----------
    report_loader : ReportLoader
        Provides Report objects for given report IDs.
    stats_provider : StatsProvider
        Computes frequency statistics for clinical contexts.
    llm_client : LLMClient, optional
        If provided, used for AE clinical-picture inconsistency assessment
        on every pair, and for the per-report narrative source/case and
        narrative-demographics extractions during batch processing.
    llm_temperature : float or None, optional
        Sampling temperature forwarded to every LLM task. ``None`` (the
        default) omits the parameter so the backend applies its own
        default — required by models that reject an explicit
        temperature, and **not** generally deterministic.

        Runs that need reproducible extractions must pass an explicit
        value (``llm_temperature=0.0``) *and* use a model that accepts
        it; a backend that cannot honour the request raises
        :class:`~llm_dup_dev.llm.LLMConfigurationError` rather than
        quietly sampling at some other temperature. The value requested
        here is recorded in the run manifest.
    pipeline_config : PipelineConfig, optional
        Feature generation configuration.
    decision_thresholds : DecisionThresholds, optional
        Classification decision boundaries.
    frozen_source_case_extractions : FrozenSourceCaseExtractions, optional
        Precomputed per-report source/case extractions. When provided,
        ``run_batch`` seeds its internal extraction cache from this
        bundle so covered reports skip live LLM calls. Partial bundles
        are allowed: missing ``report_id`` keys fall back to live
        extraction (or ``None`` if ``llm_client`` is not set).
    frozen_narrative_demographics : FrozenNarrativeDemographics, optional
        Precomputed per-report narrative demographics extractions. When
        provided, ``run_batch`` seeds its internal extraction cache from
        this bundle and merges the covered extractions into each
        ``Report`` before downstream feature generation. Partial
        bundles are allowed: missing ``report_id`` keys fall back to
        live extraction (or to an empty ``NarrativeDemographics()`` if
        ``llm_client`` is not set — merging an empty extraction is a
        no-op).
    ae_clinical_picture_mode : {"llm", "rule_based", "auto"}, optional
        Selects the AE clinical-picture assessor.

        * ``"llm"`` — use the LLM task if a client is configured;
          otherwise record ``NOT_ASSESSED`` with reason
          ``"no_llm_client"`` (no fallback).
        * ``"rule_based"`` — always use the deterministic, MedDRA-hierarchy
          based assessor in
          :mod:`llm_dup_dev.feature_generation.ae_clinical_picture_rules`.
          Suitable for organisations/settings where LLM use is not
          possible.
        * ``"auto"`` (default) — prefer the LLM task when a client is
          configured; otherwise (and on LLM error) fall back to the
          deterministic assessor with reason ``"llm_fallback_to_rule"``.
    """

    def __init__(
        self,
        report_loader: ReportLoader,
        stats_provider: StatsProvider,
        llm_client: LLMClient | None = None,
        llm_temperature: float | None = None,
        pipeline_config: PipelineConfig | None = None,
        decision_thresholds: DecisionThresholds | None = None,
        frozen_source_case_extractions: FrozenSourceCaseExtractions | None = None,
        frozen_narrative_demographics: FrozenNarrativeDemographics | None = None,
        ae_clinical_picture_mode: Literal["llm", "rule_based", "auto"] = "auto",
    ) -> None:
        self._loader = report_loader
        self._stats_provider = stats_provider
        self._llm_client = llm_client
        self._llm_temperature = llm_temperature
        self._llm_successes = 0
        self._llm_failures = 0
        self._pipeline_config = pipeline_config or PipelineConfig()
        self._decision_thresholds = decision_thresholds or DecisionThresholds()
        self._frozen_source_case = frozen_source_case_extractions
        self._frozen_narrative_demographics = frozen_narrative_demographics
        self._ae_clinical_picture_mode = ae_clinical_picture_mode

    def run_pair(
        self,
        pair: ReportPair,
        ae_clinical_picture: AEClinicalPictureResult | None = None,
        source_case_assessment: SourceCaseAssessment | None = None,
        narrative_demographics_1: NarrativeDemographics | None = None,
        narrative_demographics_2: NarrativeDemographics | None = None,
    ) -> PairResult:
        """Process a single report pair end-to-end.

        Parameters
        ----------
        pair : ReportPair
            The candidate pair to evaluate.
        ae_clinical_picture : AEClinicalPictureResult, optional
            Pre-computed AE clinical-picture assessment.  When ``None``
            and no in-pair assessment can be performed (no LLM client
            on the runner), the AE component is recorded as
            ``NOT_ASSESSED`` with reason ``"no_llm_client"``.
        source_case_assessment : SourceCaseAssessment, optional
            Pre-computed narrative source/case verdict for the pair.
            ``None`` disables the source-context evidence (shared
            provenance in the independence assessment; evidence zones
            in the relationship assessment).
        narrative_demographics_1 : NarrativeDemographics, optional
            Pre-extracted narrative demographics for the first report.
            When provided, merged into ``r1`` via
            ``merge_narrative_demographics`` after loading so the
            narrative candidates participate in the downstream
            inconsistency checks.
        narrative_demographics_2 : NarrativeDemographics, optional
            Pre-extracted narrative demographics for the second report.
            Same behavior as ``narrative_demographics_1``.

        Returns
        -------
        PairResult
            Features and classification for the pair.
        """
        # Step 1: Load reports
        r1, r2 = self._loader.load_pair(pair)

        # Step 1b: Enrich with narrative-extracted demographics so the
        # existing inconsistency logic sees structured + narrative
        # candidates together.
        if narrative_demographics_1 is not None:
            r1 = merge_narrative_demographics(r1, narrative_demographics_1)
        if narrative_demographics_2 is not None:
            r2 = merge_narrative_demographics(r2, narrative_demographics_2)

        # Step 2: Determine reporting context and compute matches
        # (needed to know what drugs/AEs to query stats for)
        context = determine_reporting_context(r1, r2)
        match_result = compute_matches(r1, r2)

        # Step 3: Determine context parameters for stats query
        country = context.country
        year = context.year

        # Step 4: Get frequency statistics from the data layer.
        # For offline providers that index stats by pair_id (PairContextSink),
        # notify them which pair is being processed before the stats call.
        if isinstance(self._stats_provider, PairContextSink):
            self._stats_provider.set_current_pair(pair.pair_id)

        stats = self._stats_provider.compute_stats(
            matching_drugs=match_result.matching_drugs,
            matching_AEs=match_result.matching_AEs,
            country=country,
            year=year,
        )

        # Step 5: Generate features (includes anchor, probability, inconsistencies)
        features = generate_pair_features(
            r1=r1,
            r2=r2,
            stats=stats,
            config=self._pipeline_config,
            pair_id=pair.pair_id,
            ae_clinical_picture=ae_clinical_picture,
            source_case_assessment=source_case_assessment,
        )

        # Step 6: Classify
        classification = classify(features, self._decision_thresholds)

        return PairResult(
            pair_id=pair.pair_id,
            features=features,
            classification=classification,
            ae_clinical_picture=ae_clinical_picture,
            source_case_assessment=source_case_assessment,
            narrative_demographics_1=narrative_demographics_1,
            narrative_demographics_2=narrative_demographics_2,
        )

    def _assess_ae_clinical_picture(
        self,
        pair_id: str,
        r1: Report,
        r2: Report,
    ) -> AEClinicalPictureResult:
        """Assess AE clinical-picture inconsistency.

        Dispatches on ``ae_clinical_picture_mode``:

        * ``"rule_based"`` — always use the deterministic assessor.
        * ``"llm"`` — use the LLM task if a client is configured; else
          ``NOT_ASSESSED("no_llm_client")``.
        * ``"auto"`` (default) — LLM if configured; on missing client or
          LLM exception, fall back to the deterministic assessor with
          ``reason="llm_fallback_to_rule"``.
        """
        mode = self._ae_clinical_picture_mode

        if mode == "rule_based":
            return assess_ae_clinical_picture_rule_based(r1, r2)

        if self._llm_client is None:
            if mode == "auto":
                return assess_ae_clinical_picture_rule_based(
                    r1, r2, fallback_from_llm=True
                )
            # mode == "llm"
            return AEClinicalPictureResult(
                status=AEClinicalPictureStatus.NOT_ASSESSED,
                reason="no_llm_client",
                method="llm",
            )

        try:
            result = assess_ae_clinical_picture(
                self._llm_client,
                pair_id=pair_id,
                r1=r1,
                r2=r2,
                temperature=self._llm_temperature,
            )
            self._note_llm_success()
            return result
        except LLMConfigurationError:
            raise
        except Exception as exc:
            logger.warning(
                "LLM AE clinical-picture assessment failed for pair %s: %s",
                pair_id,
                exc,
            )
            self._note_llm_failure(exc)
            if mode == "auto":
                return assess_ae_clinical_picture_rule_based(
                    r1, r2, fallback_from_llm=True
                )
            return AEClinicalPictureResult(
                status=AEClinicalPictureStatus.NOT_ASSESSED,
                reason="llm_error",
                method="llm",
            )

    def _seed_extraction_caches(
        self,
    ) -> tuple[
        dict[str, NarrativeSourceCase | None],
        dict[str, NarrativeDemographics],
    ]:
        """Initialise the per-batch extraction caches from frozen bundles.

        Each cache is keyed by ``report_id``.  When a corresponding
        frozen bundle was passed to the runner at construction time, its
        entries are pre-populated so covered reports skip live LLM calls
        inside ``run_batch``.
        """
        source_case_cache: dict[str, NarrativeSourceCase | None] = {}
        if self._frozen_source_case is not None:
            source_case_cache.update(self._frozen_source_case.extractions)

        demographics_cache: dict[str, NarrativeDemographics] = {}
        if self._frozen_narrative_demographics is not None:
            demographics_cache.update(
                self._frozen_narrative_demographics.extractions
            )
        return source_case_cache, demographics_cache

    def _note_llm_success(self) -> None:
        """Record a successful LLM call for the run summary."""
        self._llm_successes += 1

    def _note_llm_failure(self, exc: Exception) -> None:
        """Record a tolerated LLM failure for the run summary.

        Deliberately does not abort. Failures reaching here are the
        per-report kind — an unparseable completion, a transient error —
        and swallowing them is what stops one malformed narrative from
        taking down a batch.

        Fatal misconfiguration takes the other path: clients raise
        :class:`LLMConfigurationError`, which the extraction methods
        re-raise rather than swallow. Counting here cannot substitute
        for that, because a run of failures is indistinguishable from
        bad luck at the point it happens — which is why the counts are
        reported at the end of the batch instead.
        """
        self._llm_failures += 1

    def _log_llm_call_summary(self) -> None:
        """Report LLM success/failure counts at the end of a batch.

        The per-call warnings are individually easy to miss in a large
        run. A run where *nothing* succeeded completes normally with
        every extraction empty, which downstream is indistinguishable
        from legitimately absent data — so it gets an ERROR, not a
        warning.
        """
        if not self._llm_failures:
            return
        total = self._llm_successes + self._llm_failures
        if self._llm_successes == 0:
            logger.error(
                "All %d LLM calls in this run failed. Every LLM-derived "
                "feature is empty; results are not trustworthy. This "
                "usually means a misconfigured client (unsupported "
                "parameter, wrong deployment, bad credentials) rather "
                "than per-report failures.",
                total,
            )
        else:
            logger.warning(
                "%d of %d LLM calls in this run failed; the affected "
                "reports/pairs have empty LLM-derived features.",
                self._llm_failures,
                total,
            )

    def _extract_source_case(self, report: Report) -> NarrativeSourceCase | None:
        """Extract narrative source/case signals for one report.

        Returns ``None`` when no LLM client is configured or extraction
        fails — never aborts the batch. Errors are logged and the pair
        downstream receives ``source_case_assessment=None``.
        """
        if self._llm_client is None:
            return None
        try:
            result = extract_source_case(
                self._llm_client,
                report.report_id,
                report.narrative,
                literature_reference=report.literature_reference,
                temperature=self._llm_temperature,
            )
            self._note_llm_success()
            return result
        except LLMConfigurationError:
            raise
        except Exception as exc:
            logger.warning(
                "LLM narrative source/case extraction failed for "
                "report %s: %s",
                report.report_id,
                exc,
            )
            self._note_llm_failure(exc)
            return None

    def _extract_narrative_demographics(
        self, report: Report
    ) -> NarrativeDemographics:
        """Extract narrative demographic candidates for one report.

        Returns an empty ``NarrativeDemographics()`` when no LLM client
        is configured or extraction fails — never aborts the batch.
        Merging an empty extraction is a no-op, so downstream code does
        not need a missing-value guard.
        """
        if self._llm_client is None:
            return NarrativeDemographics()
        try:
            result = extract_narrative_demographics(
                self._llm_client,
                report.report_id,
                report.narrative,
                temperature=self._llm_temperature,
            )
            self._note_llm_success()
            return result
        except LLMConfigurationError:
            raise
        except Exception as exc:
            logger.warning(
                "LLM narrative demographics extraction failed for "
                "report %s: %s",
                report.report_id,
                exc,
            )
            self._note_llm_failure(exc)
            return NarrativeDemographics()

    def _assess_source_case(
        self,
        a: NarrativeSourceCase | None,
        b: NarrativeSourceCase | None,
    ) -> SourceCaseAssessment | None:
        """Run the deterministic pair-level comparator.

        Returns ``None`` when either extraction is missing (so the
        source-context evidence is disabled for this pair). The
        comparator itself also returns a ``NOT_ASSESSED`` verdict for
        this case, but ``None`` is a stronger signal downstream — it
        lets the decision logic skip the source-context zones entirely
        rather than inspect a verdict enum.
        """
        if a is None or b is None:
            return None
        try:
            return compare_source_case(a, b)
        except Exception as exc:
            logger.warning("Source/case comparison failed: %s", exc)
            return None

    def run_batch(
        self,
        pairs: Sequence[ReportPair],
        continue_on_error: bool = True,
    ) -> BatchResult:
        """Process multiple pairs.

        Parameters
        ----------
        pairs : Sequence[ReportPair]
            Pairs to evaluate.
        continue_on_error : bool
            If True, log errors and continue. If False, raise on first error.

        Returns
        -------
        BatchResult
            All results and any errors encountered.
        """
        batch = BatchResult()

        # Loud warning when the per-report LLM extraction paths are silently
        # disabled — neither a live ``llm_client`` nor a frozen sidecar is
        # configured. Without one of these the corresponding feature degrades
        # silently: the source-context evidence never contributes, and the
        # narrative-demographics merge is a no-op. Surfacing this once per
        # ``run_batch`` is the smallest signal that catches misconfigured
        # offline deployments. See ``docs/integration_guide.md`` §
        # *Wiring without an LLM client*.
        if self._llm_client is None and self._frozen_source_case is None:
            logger.warning(
                "Source-context evidence is disabled: no llm_client and no "
                "frozen_source_case_extractions bundle configured. "
                "Pre-extract per-report source/case at dataset-creation time "
                "and pass the bundle via frozen_source_case_extractions= "
                "to enable it."
            )
        if (
            self._llm_client is None
            and self._frozen_narrative_demographics is None
        ):
            logger.warning(
                "Narrative-demographics extraction is disabled: no llm_client "
                "and no frozen_narrative_demographics bundle configured. "
                "Pre-extract per-report narrative demographics at "
                "dataset-creation time and pass the bundle via "
                "frozen_narrative_demographics= to enable LLM-derived "
                "patient age/sex/weight/height candidates."
            )

        # Memoize per-report extractions for the duration of one
        # ``run_batch`` call.  Both caches are seeded from their frozen
        # bundles when configured — covered reports skip live LLM
        # calls.  Cache misses inside the loop below trigger live
        # extraction.
        source_case_cache, demographics_cache = self._seed_extraction_caches()

        for pair in pairs:
            try:
                # Load reports to get narratives
                r1, r2 = self._loader.load_pair(pair)

                # AE clinical-picture assessment (LLM-driven; graceful
                # NOT_ASSESSED fallback when no LLM client or on error).
                ae_result = self._assess_ae_clinical_picture(
                    pair.pair_id, r1, r2
                )

                # Narrative source/case: extract per report (memoized)
                # then compare deterministically.
                if r1.report_id not in source_case_cache:
                    source_case_cache[r1.report_id] = self._extract_source_case(r1)
                if r2.report_id not in source_case_cache:
                    source_case_cache[r2.report_id] = self._extract_source_case(r2)
                source_case = self._assess_source_case(
                    source_case_cache[r1.report_id],
                    source_case_cache[r2.report_id],
                )

                # Narrative demographics: extract per report (memoized);
                # the merge into each ``Report`` happens inside
                # ``run_pair``.
                if r1.report_id not in demographics_cache:
                    demographics_cache[r1.report_id] = (
                        self._extract_narrative_demographics(r1)
                    )
                if r2.report_id not in demographics_cache:
                    demographics_cache[r2.report_id] = (
                        self._extract_narrative_demographics(r2)
                    )

                result = self.run_pair(
                    pair,
                    ae_clinical_picture=ae_result,
                    source_case_assessment=source_case,
                    narrative_demographics_1=demographics_cache[r1.report_id],
                    narrative_demographics_2=demographics_cache[r2.report_id],
                )
                batch.results.append(result)

            except LLMConfigurationError:
                # Not per-pair bad luck: the client is misconfigured and
                # every remaining pair would fail the same way. Ignores
                # ``continue_on_error``, which exists to tolerate bad
                # data, not a broken backend.
                self._log_llm_call_summary()
                raise
            except Exception as exc:
                if not continue_on_error:
                    raise
                logger.warning(
                    "Error processing pair %s: %s", pair.pair_id, exc
                )
                batch.errors.append((pair.pair_id, exc))

        self._log_llm_call_summary()
        logger.info(
            "Batch complete: %d processed, %d errors",
            batch.n_processed,
            batch.n_errors,
        )
        return batch
