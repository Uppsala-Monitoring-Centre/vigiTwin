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
    result = runner.run_pair(pair, narrative_consistency)

    # Batch
    results = runner.run_batch(pairs, narrative_provider)

With LLM-based narrative comparison::

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
from typing import Callable, Sequence

from llm_dup_dev.classifier.config import DecisionThresholds, PipelineConfig
from llm_dup_dev.classifier.decision_logic import classify
from llm_dup_dev.data.protocols import PairContextSink, ReportLoader, StatsProvider
from llm_dup_dev.feature_generation.matching import compute_matches
from llm_dup_dev.feature_generation.pipeline import generate_pair_features
from llm_dup_dev.feature_generation.reporting_context import (
    determine_reporting_context,
)
from llm_dup_dev.llm.protocols import LLMClient
from llm_dup_dev.llm.tasks.narrative_comparison import (
    NarrativeVerdict,
    compare_narratives,
)
from llm_dup_dev.models.classification import ClassificationResult
from llm_dup_dev.models.enums import NarrativeConsistency, ReportingContextLevel
from llm_dup_dev.models.pair_features import PairFeatures
from llm_dup_dev.models.report_pair import ReportPair

logger = logging.getLogger(__name__)


# Type alias for a function that provides narrative consistency.
# Takes (pair_id, narrative_1, narrative_2) → NarrativeConsistency.
# Retained for backward compatibility; prefer injecting an LLMClient.
NarrativeProvider = Callable[[str, str | None, str | None], NarrativeConsistency]


def neutral_narrative_provider(
    pair_id: str,
    narrative_1: str | None,
    narrative_2: str | None,
) -> NarrativeConsistency:
    """Default narrative provider that always returns NOT_AVAILABLE.

    Used when no LLM client or explicit narrative provider is configured.
    """
    return NarrativeConsistency.NOT_AVAILABLE


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
    narrative_verdict : NarrativeVerdict or None
        Full LLM narrative assessment including evidence and reasoning,
        when an LLM client was used.  ``None`` when the narrative was
        provided via a simple ``NarrativeProvider`` callback or was not
        assessed.
    """

    pair_id: str
    features: PairFeatures
    classification: ClassificationResult
    narrative_verdict: NarrativeVerdict | None = None

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
        4. Narrative assessment (via LLMClient or NarrativeProvider)
        5. Feature generation (probability, inconsistencies, anchor)
        6. Decision logic classification

    Parameters
    ----------
    report_loader : ReportLoader
        Provides Report objects for given report IDs.
    stats_provider : StatsProvider
        Computes frequency statistics for clinical contexts.
    llm_client : LLMClient, optional
        If provided, used for LLM-based narrative comparison during
        batch processing.  Takes precedence over ``narrative_provider``
        passed to :meth:`run_batch`.
    pipeline_config : PipelineConfig, optional
        Feature generation configuration.
    decision_thresholds : DecisionThresholds, optional
        Classification decision boundaries.
    """

    def __init__(
        self,
        report_loader: ReportLoader,
        stats_provider: StatsProvider,
        llm_client: LLMClient | None = None,
        pipeline_config: PipelineConfig | None = None,
        decision_thresholds: DecisionThresholds | None = None,
    ) -> None:
        self._loader = report_loader
        self._stats_provider = stats_provider
        self._llm_client = llm_client
        self._pipeline_config = pipeline_config or PipelineConfig()
        self._decision_thresholds = decision_thresholds or DecisionThresholds()

    def run_pair(
        self,
        pair: ReportPair,
        narrative_consistency: NarrativeConsistency = NarrativeConsistency.NOT_AVAILABLE,
        narrative_verdict: NarrativeVerdict | None = None,
    ) -> PairResult:
        """Process a single report pair end-to-end.

        Parameters
        ----------
        pair : ReportPair
            The candidate pair to evaluate.
        narrative_consistency : NarrativeConsistency, optional
            Pre-computed narrative assessment.  Defaults to NOT_AVAILABLE.
            Ignored when ``narrative_verdict`` is provided (the verdict's
            consistency is used instead).
        narrative_verdict : NarrativeVerdict, optional
            Full LLM narrative assessment.  When provided, its
            ``consistency`` field is used and the ``narrative_consistency``
            parameter is ignored.

        Returns
        -------
        PairResult
            Features and classification for the pair.
        """
        # Resolve narrative consistency
        if narrative_verdict is not None:
            effective_consistency = narrative_verdict.consistency
        else:
            effective_consistency = narrative_consistency

        # Step 1: Load reports
        r1, r2 = self._loader.load_pair(pair)

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
            narrative_consistency=effective_consistency,
            config=self._pipeline_config,
            pair_id=pair.pair_id,
        )

        # Step 6: Classify
        classification = classify(features, self._decision_thresholds)

        return PairResult(
            pair_id=pair.pair_id,
            features=features,
            classification=classification,
            narrative_verdict=narrative_verdict,
        )

    def _assess_narrative(
        self,
        pair_id: str,
        narrative_1: str | None,
        narrative_2: str | None,
    ) -> tuple[NarrativeConsistency, NarrativeVerdict | None]:
        """Assess narrative consistency via the injected LLM client.

        Returns
        -------
        tuple[NarrativeConsistency, NarrativeVerdict | None]
            The consistency level and the full verdict (if LLM was used).
            Falls back to NOT_AVAILABLE with no verdict on failure.
        """
        if self._llm_client is None:
            return NarrativeConsistency.NOT_AVAILABLE, None

        try:
            verdict = compare_narratives(
                self._llm_client,
                pair_id=pair_id,
                narrative_1=narrative_1,
                narrative_2=narrative_2,
            )
            return verdict.consistency, verdict
        except Exception as exc:
            logger.warning(
                "LLM narrative assessment failed for pair %s: %s",
                pair_id,
                exc,
            )
            return NarrativeConsistency.NOT_AVAILABLE, None

    def run_batch(
        self,
        pairs: Sequence[ReportPair],
        narrative_provider: NarrativeProvider | None = None,
        continue_on_error: bool = True,
    ) -> BatchResult:
        """Process multiple pairs.

        When an ``llm_client`` was provided at construction time, it is
        used for narrative assessment automatically.  If the LLM call
        fails for a pair the narrative is recorded as NOT_AVAILABLE and
        processing continues.

        An explicit ``narrative_provider`` callback takes precedence over
        the LLM client — this allows callers to supply pre-computed
        assessments when available.

        Parameters
        ----------
        pairs : Sequence[ReportPair]
            Pairs to evaluate.
        narrative_provider : NarrativeProvider, optional
            Callable that provides narrative consistency for each pair.
            If None and no ``llm_client`` is configured, NOT_AVAILABLE
            is used for all pairs.
        continue_on_error : bool
            If True, log errors and continue. If False, raise on first error.

        Returns
        -------
        BatchResult
            All results and any errors encountered.
        """
        batch = BatchResult()

        for pair in pairs:
            try:
                # Load reports to get narratives
                r1, r2 = self._loader.load_pair(pair)

                # Determine narrative assessment
                verdict: NarrativeVerdict | None = None

                if narrative_provider is not None:
                    # Explicit callback takes precedence
                    consistency = narrative_provider(
                        pair.pair_id, r1.narrative, r2.narrative
                    )
                elif self._llm_client is not None:
                    # Use LLM client with graceful fallback
                    consistency, verdict = self._assess_narrative(
                        pair.pair_id, r1.narrative, r2.narrative
                    )
                else:
                    consistency = NarrativeConsistency.NOT_AVAILABLE

                result = self.run_pair(
                    pair,
                    narrative_consistency=consistency,
                    narrative_verdict=verdict,
                )
                batch.results.append(result)

            except Exception as exc:
                if not continue_on_error:
                    raise
                logger.warning(
                    "Error processing pair %s: %s", pair.pair_id, exc
                )
                batch.errors.append((pair.pair_id, exc))

        logger.info(
            "Batch complete: %d processed, %d errors",
            batch.n_processed,
            batch.n_errors,
        )
        return batch
