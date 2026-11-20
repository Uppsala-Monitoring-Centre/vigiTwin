"""Data access layer — protocols and implementations.

Defines the abstract contracts (protocols) for loading reports and
computing frequency statistics. Concrete implementations live either
here (synthetic/testing) or in the secure repo (real database access).

Submodules:
    protocols   Abstract interfaces for data access (ReportLoader, StatsProvider,
                PairContextSink).
    synthetic   In-memory implementation using synthetic data for testing.
    offline     Offline/frozen-validation implementation backed by precomputed
                per-pair frequency bundles (FrozenStatsProvider).
"""
