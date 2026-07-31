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

## [0.5.0] - 2026-07-31

Explicit inter-report links: the adjudicator now reads the reports'
structured case-identifier fields (ICH E2B(R3) C.1.9 "Other case
identifiers in previous transmissions"). When one report of a pair names
the other, the sending organisation has declared them to be the same
case — the most direct evidence of shared provenance the data can offer,
and the pipeline previously ignored it.

Minor rather than patch: `IndependenceReason` gains a member that
consumers may switch on, and the review HTML gains a consumer-visible
section and index column.

### Added
- `Report.case_identifiers` and `Report.other_case_identifiers`, both
  optional and defaulting to `[]`, and deliberately asymmetric in shape.
  `case_identifiers` holds the report's own case numbers as
  `CaseIdentifierRef` entries typed by `CaseIdentifierKind`
  (`safety_report_id` / `company_number` / `authority_number`), since the
  source system records them in three named fields.
  `other_case_identifiers` holds **plain strings** — the C.1.9 field
  carries the identifier only, with no kind qualifier.
  **Secure-repo action required** to realise the feature:
  `ReportLoader.load_report` must populate both — a pointer on one report
  can only be resolved against the other report's own identifiers. See
  `integration_guide.md` § ReportLoader.
- `feature_generation.explicit_link_comparison.compare_explicit_links` —
  deterministic, LLM-free, DB-free pair comparator. Matches on the
  normalized value alone, testing each pointer against every identifier
  the other report holds regardless of which of its fields that value
  came from (any of the three can carry a worldwide-unique identifier).
  Drops self-pointers and degenerate values, and requires a *direct*
  A→B / B→A reference — a shared pointer at a common third case does not
  link.
- `PairFeatures.explicit_link` carrying an `ExplicitLinkAssessment`
  (`explicit_link` / `no_link` / `not_assessed`). `None` on features
  generated before this release; `not_assessed` means neither report
  carried identifier data and must be read as *no information*.
- `IndependenceReason.EXPLICIT_LINK`. An explicit link rejects
  independence outright and is recorded in preference to
  `SHARED_SOURCE` when both apply.
- Relationship-assessment **explicit-link zone** at priority 3 — below
  Identity and Distinct, above Unresolved — adjudicating at the
  compelling budget (`w_tot < w_tot_compelling_zone`, default 4.0) and
  failing to `Different_case` rather than `Abstain`. No new threshold
  parameter.
- `DecisionThresholds.explicit_link_enabled` (default `True`). Placed on
  `DecisionThresholds` so `classify` stays a pure function of
  `(PairFeatures, DecisionThresholds)` and `eval.sweep` can replay
  archived features with the signal on and off. This matters for
  evaluation: some regulator reference sets were partly assembled from
  E2B link fields, so recall on those pairs is circular while the signal
  is enabled — see `duplicate_adjudication_spec.md` § Calibrating them.
  Surfaced in run manifests as `feature_flags.explicit_link_enabled`.
- Review HTML: an "Explicit Case Links" section on the pair page (match
  table plus both reports' raw identifier lists, shown for `no_link` too
  so a reviewer can see why nothing resolved) and an "Explicit link"
  column on the batch index, present only when some pair is linked.

## [0.4.0] - 2026-07-31

Removal of the standalone LLM narrative-consistency assessment, which was
built but never wired into the decision logic.

### Removed
- `llm.tasks.narrative_comparison` (the `NarrativeVerdict` schema,
  `build_narrative_request` and `compare_narratives`). Its verdict reached
  `PairFeatures.narrative_consistency` and stopped there: unlike every other
  assessor it never produced an `Inconsistency`, so it contributed nothing to
  `inconsistency_counts`, the probability, or the final label. The classifier
  never read the field.
- `PairFeatures.narrative_consistency` and the `NarrativeConsistency` enum
  (also dropped from the `llm_dup_dev.models` public exports).
- `PairResult.narrative_verdict`, and the `narrative_verdict` key from the
  `pair_results.json` artifact.
- The `NarrativeProvider` type alias, `neutral_narrative_provider`, and the
  `narrative_provider` parameter of `PipelineRunner.run_batch`. The callback
  existed only to supply a value nothing consumed.
- The `narrative_consistency` parameter of `generate_pair_features`, the
  "Narrative consistency" row on the HTML pair page, and the unrendered
  `narrative_consistency` field on the review index view model.

### Changed
- A configured `llm_client` now costs **five** LLM calls per pair instead of
  six: the per-pair narrative comparison is gone, leaving the AE
  clinical-picture call plus the two per-report source/case and two per-report
  demographics extractions.

### Migration
Reading an existing `pair_results.json` or `features.json` still works — the
removed keys are ignored on load, since no model forbids extra fields. Callers
passing `narrative_consistency=` or `narrative_verdict=` to `run_pair`, or
`narrative_provider=` to `run_batch`, must drop those arguments.

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
