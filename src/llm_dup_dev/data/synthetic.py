"""In-memory synthetic data implementation for testing.

Provides concrete implementations of ReportLoader and StatsProvider
that operate on dictionaries of pre-built Report objects and
configurable frequency statistics. No database access required.

Usage::

    from llm_dup_dev.data.synthetic import SyntheticReportLoader, SyntheticStatsProvider

    loader = SyntheticReportLoader({"R1": report_1, "R2": report_2})
    stats_provider = SyntheticStatsProvider(default_N_total=5000)

    r1, r2 = loader.load_pair(pair)
    stats = stats_provider.compute_stats(matching_drugs, matching_AEs, country, year)
"""

from __future__ import annotations

from typing import Iterator, Sequence

from llm_dup_dev.feature_generation.probability import FrequencyStats
from llm_dup_dev.models.report import Report
from llm_dup_dev.models.report_pair import ReportPair


class SyntheticReportLoader:
    """Loads reports from an in-memory dictionary.

    Satisfies the ``ReportLoader`` protocol.
    """

    def __init__(self, reports: dict[str, Report]) -> None:
        """
        Parameters
        ----------
        reports : dict[str, Report]
            Mapping of report_id → Report.
        """
        self._reports = dict(reports)

    def load_report(self, report_id: str) -> Report:
        """Load a single report by ID.

        Raises
        ------
        KeyError
            If report_id is not in the store.
        """
        return self._reports[report_id]

    def load_pair(self, pair: ReportPair) -> tuple[Report, Report]:
        """Load both reports for a candidate pair."""
        return (
            self._reports[pair.report_1_id],
            self._reports[pair.report_2_id],
        )

    def load_pairs(
        self, pairs: Sequence[ReportPair]
    ) -> Iterator[tuple[ReportPair, Report, Report]]:
        """Yield (pair, report_1, report_2) for each pair."""
        for pair in pairs:
            r1, r2 = self.load_pair(pair)
            yield pair, r1, r2

    @property
    def report_ids(self) -> list[str]:
        """List all available report IDs."""
        return list(self._reports.keys())


class SyntheticStatsProvider:
    """Provides configurable frequency statistics for testing.

    Satisfies the ``StatsProvider`` protocol.

    By default, returns plausible counts that produce meaningful
    L_match values. Individual pair overrides can be registered
    for testing specific scenarios.
    """

    def __init__(
        self,
        default_N_total: int = 5000,
        default_N_drugs: int = 200,
        default_N_AEs: int = 300,
        default_N_drugs_AEs: int = 40,
    ) -> None:
        """
        Parameters
        ----------
        default_N_total : int
            Default total reports in context.
        default_N_drugs : int
            Default count for reports containing the matching drug set.
        default_N_AEs : int
            Default count for reports containing the matching AE set.
        default_N_drugs_AEs : int
            Default count for reports containing both.
        """
        self._default = FrequencyStats(
            N_total=default_N_total,
            N_drugs=default_N_drugs,
            N_AEs=default_N_AEs,
            N_drugs_AEs=default_N_drugs_AEs,
        )
        self._overrides: dict[str, FrequencyStats] = {}

    def register_override(
        self,
        key: str,
        stats: FrequencyStats,
    ) -> None:
        """Register an override for a specific context key.

        Parameters
        ----------
        key : str
            Context key, typically built by ``_context_key()``.
        stats : FrequencyStats
            The statistics to return for this context.
        """
        self._overrides[key] = stats

    def compute_stats(
        self,
        matching_drugs: Sequence[str],
        matching_AEs: Sequence[str],
        country: str | None,
        year: int | None,
    ) -> FrequencyStats:
        """Return frequency statistics for the clinical context.

        Checks for a registered override first (keyed by drugs+AEs+context),
        then falls back to defaults.
        """
        key = self._context_key(matching_drugs, matching_AEs, country, year)
        if key in self._overrides:
            return self._overrides[key]
        return self._default

    @staticmethod
    def _context_key(
        matching_drugs: Sequence[str],
        matching_AEs: Sequence[str],
        country: str | None,
        year: int | None,
    ) -> str:
        """Build a deterministic lookup key from context parameters."""
        drugs_part = ",".join(sorted(matching_drugs))
        aes_part = ",".join(sorted(matching_AEs))
        return f"{drugs_part}|{aes_part}|{country}|{year}"
