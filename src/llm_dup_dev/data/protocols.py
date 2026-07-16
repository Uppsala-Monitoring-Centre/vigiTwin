"""Abstract protocols for data access.

These protocols define the contract between the pipeline and the data
layer. The secure repo implements these against a real database; this
repo provides a synthetic implementation for testing.

Usage in the secure repo::

    from llm_dup_dev.data.protocols import ReportLoader, StatsProvider

    class SqlReportLoader:
        '''Implements ReportLoader against SQL Server.'''
        def load_report(self, report_id: str) -> Report: ...
        def load_pair(self, pair: ReportPair) -> tuple[Report, Report]: ...
        def load_pairs(self, pairs: Sequence[ReportPair]) -> ...: ...

    assert isinstance(SqlReportLoader(), ReportLoader)  # structural check
"""

from __future__ import annotations

from typing import Iterator, Protocol, Sequence, runtime_checkable

from llm_dup_dev.feature_generation.probability import FrequencyStats
from llm_dup_dev.models.report import Report
from llm_dup_dev.models.report_pair import ReportPair


@runtime_checkable
class ReportLoader(Protocol):
    """Loads structured Report objects from the data source.

    Implementations must convert raw database rows / JSON into the
    ``Report`` Pydantic model. Multi-valued attributes should be
    populated from all available sources (database + LLM extraction).
    """

    def load_report(self, report_id: str) -> Report:
        """Load a single report by ID.

        Parameters
        ----------
        report_id : str
            Unique identifier of the report in the database.

        Returns
        -------
        Report

        Raises
        ------
        KeyError
            If the report_id is not found.
        """
        ...

    def load_pair(self, pair: ReportPair) -> tuple[Report, Report]:
        """Load both reports for a candidate pair.

        Parameters
        ----------
        pair : ReportPair
            The candidate pair to load.

        Returns
        -------
        tuple[Report, Report]
            (report_1, report_2) in pair order.
        """
        ...

    def load_pairs(
        self, pairs: Sequence[ReportPair]
    ) -> Iterator[tuple[ReportPair, Report, Report]]:
        """Load reports for multiple pairs.

        Yields
        ------
        tuple[ReportPair, Report, Report]
            (pair, report_1, report_2) for each pair.

        Notes
        -----
        Implementations may batch database queries for efficiency.
        """
        ...


@runtime_checkable
class PairContextSink(Protocol):
    """Optional companion protocol for StatsProviders that need pair context.

    A StatsProvider may also implement this protocol to receive the
    current ``pair_id`` before each ``compute_stats`` call.
    ``PipelineRunner`` checks for this protocol at runtime and calls
    ``set_current_pair`` when available — existing providers that do not
    implement it are unaffected.

    This is the recommended extension point for offline providers that
    look up precomputed statistics by ``pair_id``.

    Example::

        class MyOfflineProvider:
            def set_current_pair(self, pair_id: str) -> None:
                self._current_pair_id = pair_id

            def compute_stats(self, ...) -> FrequencyStats:
                bundle = self._bundles[self._current_pair_id]
                ...
    """

    def set_current_pair(self, pair_id: str) -> None:
        """Record which pair is currently being processed.

        Called by PipelineRunner before ``compute_stats`` for each pair.
        """
        ...


@runtime_checkable
class StatsProvider(Protocol):
    """Provides frequency statistics for a report pair's shared clinical context.

    Given two reports and their matching feature sets, computes the
    database-level counts needed for probability estimation:

    - N_total: total reports in the reporting context
    - N_drugs: reports containing all matching drugs
    - N_AEs: reports containing all matching AEs
    - N_drugs_AEs: reports containing both

    These counts depend on the reporting context (country+year / country / global)
    which is determined by the pipeline before calling this provider.

    **The counts must include the two focal reports.** Both the
    leave-pair-out marginals and anchor selection depend on this: a
    non-empty ``matching_drugs`` implies ``N_drugs >= 2``, because both
    reports contain what they were found to share. Returning counts that
    exclude the focal pair will corrupt the probability estimates.

    It follows that a count of zero must mean **the matching set was
    empty**, and never "no reports were found". Callers rely on this to
    distinguish "this feature does not narrow the population" (count 0 →
    the candidate is the whole context) from "this feature narrows it to
    a rare group" (small non-zero count → strong evidence). Conflating
    the two inverts the method's conclusion.

    Implementations *should* additionally populate the optional
    broader-context fields on the returned ``FrequencyStats`` when the
    data source has them: ``N_total_country`` / ``N_drugs_country`` /
    ``N_AEs_country`` (when the primary scope is country-year, populate
    these from country-level counts) and ``N_total_global`` /
    ``N_drugs_global`` / ``N_AEs_global`` (whenever the primary scope is
    country-year or country, populate these from global counts). These
    feed the first-level-with-evidence backoff cascade for drug-set /
    AE-set marginals — without them, a feature that exists only in the
    candidate pair degrades to a small floor probability rather than a
    data-driven country/global LPO rate. Only set fields that are
    strictly broader than the primary scope (otherwise the same data
    would be consulted twice); leave the rest as ``None``.
    """

    def compute_stats(
        self,
        matching_drugs: Sequence[str],
        matching_AEs: Sequence[str],
        country: str | None,
        year: int | None,
    ) -> FrequencyStats:
        """Compute frequency statistics for the given clinical context.

        Parameters
        ----------
        matching_drugs : Sequence[str]
            Set of matching drug substances (canonical sorted order).
        matching_AEs : Sequence[str]
            Set of matching AE preferred terms (canonical sorted order).
        country : str or None
            Reporting country for context filtering. None for global.
        year : int or None
            Reporting year for context filtering. None for country-only
            or global context.

        Returns
        -------
        FrequencyStats
            Database-level counts within the reporting context. Should
            also populate optional broader-context fields when
            available; see the class docstring for details.
        """
        ...
