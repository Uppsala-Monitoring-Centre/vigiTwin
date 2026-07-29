# Changelog

All notable changes to `llm-dup-dev` are recorded here.

The version follows [Semantic Versioning](https://semver.org). It is the
**consumer-contract** signal — the JSON schemas in `schemas/` plus the public
API the secure companion repo imports — not a per-commit identifier. Exact code
provenance lives in the `dev_git_commit` field of each manifest; this file and
the version number carry the *meaning* of a change. See `agents.md` §
Conventions → Versioning for the bump rules.

While pre-1.0: a breaking schema/API change bumps the **minor**
(`0.2.0` → `0.3.0`); additive or backward-compatible changes and fixes bump the
**patch** (`0.2.0` → `0.2.1`).

## [Unreleased]

## [0.3.0] - 2026-07-29

Support for reference sets where no screening-algorithm results exist, so that
adjudicator performance can be evaluated on the supplied set without screening
recall being observed or assumed.

### Added
- `ReferenceSet.screening_mode` (`observed` | `not_evaluated`), selected per
  reference set and never inferred. `not_evaluated` declares that screening
  performance is outside that set's estimand: every supplied pair goes directly
  to adjudicator evaluation, screening and end-to-end estimands are not
  computed, and no synthetic screening verdicts are created. It is not a claim
  that screening was perfect. Only a recall set may use it — the combination is
  rejected on a precision set, which *is* the screening candidate set.
- `study_eval.preflight`, a reusable pre-analysis validation module whose
  screening requirements are conditional on each set's mode. It accumulates all
  problems and raises them together, and is called by both the analysis
  functions and `workflow.run_analysis`.
- `RecallResult.screening_evaluated_set_ids` /
  `screening_not_evaluated_set_ids`, exposing the scope any screening figure
  covers when a run mixes the two modes.
- `EvaluationRunManifest.screening_modes` records the mode of every reference
  set in the run.
- `screening_mode` columns on `recall_metrics.csv`,
  `recall_joint_classification.csv` and `predictions_table.csv`, and
  `screening_mode` fields on `PrecisionResult` / `RecallSetResult`.
- `aggregate.ADJUDICATOR_STRATA`, the two-value stratification SCLE sampling
  uses for a set with no screening dimension.

### Changed
- **Breaking:** `ReferenceSet.screening_required` is replaced by
  `screening_mode`. It was only ever read to relax the precision-set candidate
  check, which is now unconditional for precision sets.
- **Breaking:** `PairPrediction.screening_retrieved` is now optional
  (`bool | None`), so a prediction bundle need carry no screening verdict.
  It remains required for every pair of an `observed` set, enforced by
  preflight.
- **Breaking:** `RecallSetResult.recall_screening`, `recall_serial`,
  `joint_counts` and `joint_proportions` are now nullable, and are `None` under
  `not_evaluated`. `None` means *not estimated*, and stays distinct from an
  estimate that is undefined at n=0 (a present estimate with `proportion: null`
  and `denominator: 0`). Screening rows are omitted from the recall CSVs for
  such a set rather than written blank.
- `SCLEAutoContext.methods_disagree` is nullable: without a screening verdict
  there is no second method to disagree with, so the disagreement dimension does
  not apply. Reviewer workbooks show `not_applicable` for it and for
  `Screening retrieved` on those cases.
- A `screening_retrieved` value supplied for a pair in a `not_evaluated` set is
  accepted and never read — the mode describes the estimand, not whether
  screening data exist — so reference sets may overlap across modes. Preflight
  warns rather than failing, so the value is not ignored silently.

## [0.2.0] - 2026-07-27

Baseline release. The version had remained `0.1.0` since the project's early
days while the schemas and public API evolved substantially; this is a one-time
honest catch-up bump to re-anchor the contract signal, and the point from which
`CHANGELOG.md` is maintained going forward.

### Added
- `CHANGELOG.md` and a documented versioning policy (`agents.md` § Conventions).
