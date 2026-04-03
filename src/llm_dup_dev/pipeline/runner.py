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
from llm_dup_dev.models.classification import ClassificationResult
from llm_dup_dev.models.enums import NarrativeConsistency, ReportingContextLevel
from llm_dup_dev.models.pair_features import PairFeatures
from llm_dup_dev.models.report_pair import ReportPair

logger = logging.getLogger(__name__)


# Type alias for a function that provides narrative consistency.
# Takes (pair_id, narrative_1, narrative_2) → NarrativeConsistency.
# This will later be backed by the LLM-based narrative module.
NarrativeProvider = Callable[[str, str | None, str | None], NarrativeConsistency]


def neutral_narrative_provider(
    pair_id: str,
    narrative_1: str | None,
    narrative_2: str | None,
) -> NarrativeConsistency:
    """Default narrative provider that always returns NEUTRAL.

    Used when the narrative analysis module is not yet available.
    """
    return NarrativeConsistency.NEUTRAL


@dataclass
class PairResult:
    """Complete result for a single pair: features + classification."""

    pair_id: str
    features: PairFeatures
    classification: ClassificationResult

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
        4. Feature generation (probability, inconsistencies, anchor)
        5. Decision logic classification

    Parameters
    ----------
    report_loader : ReportLoader
        Provides Report objects for given report IDs.
    stats_provider : StatsProvider
        Computes frequency statistics for clinical contexts.
    pipeline_config : PipelineConfig, optional
        Feature generation configuration.
    decision_thresholds : DecisionThresholds, optional
        Classification decision boundaries.
    """

    def __init__(
        self,
        report_loader: ReportLoader,
        stats_provider: StatsProvider,
        pipeline_config: PipelineConfig | None = None,
        decision_thresholds: DecisionThresholds | None = None,
    ) -> None:
        self._loader = report_loader
        self._stats_provider = stats_provider
        self._pipeline_config = pipeline_config or PipelineConfig()
        self._decision_thresholds = decision_thresholds or DecisionThresholds()

    def run_pair(
        self,
        pair: ReportPair,
        narrative_consistency: NarrativeConsistency = NarrativeConsistency.NEUTRAL,
    ) -> PairResult:
        """Process a single report pair end-to-end.

        Parameters
        ----------
        pair : ReportPair
            The candidate pair to evaluate.
        narrative_consistency : NarrativeConsistency, optional
            Pre-computed narrative assessment. Defaults to NEUTRAL.

        Returns
        -------
        PairResult
            Features and classification for the pair.
        """
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
            narrative_consistency=narrative_consistency,
            config=self._pipeline_config,
            pair_id=pair.pair_id,
        )

        # Step 6: Classify
        classification = classify(features, self._decision_thresholds)

        return PairResult(
            pair_id=pair.pair_id,
            features=features,
            classification=classification,
        )

    def run_batch(
        self,
        pairs: Sequence[ReportPair],
        narrative_provider: NarrativeProvider | None = None,
        continue_on_error: bool = True,
    ) -> BatchResult:
        """Process multiple pairs.

        Parameters
        ----------
        pairs : Sequence[ReportPair]
            Pairs to evaluate.
        narrative_provider : NarrativeProvider, optional
            Callable that provides narrative consistency for each pair.
            If None, uses NEUTRAL for all pairs.
        continue_on_error : bool
            If True, log errors and continue. If False, raise on first error.

        Returns
        -------
        BatchResult
            All results and any errors encountered.
        """
        if narrative_provider is None:
            narrative_provider = neutral_narrative_provider

        batch = BatchResult()

        for pair in pairs:
            try:
                # Load reports to get narratives for the provider
                r1, r2 = self._loader.load_pair(pair)
                narrative = narrative_provider(
                    pair.pair_id, r1.narrative, r2.narrative
                )

                result = self.run_pair(pair, narrative)
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
