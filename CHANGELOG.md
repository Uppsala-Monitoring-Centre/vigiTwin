# Changelog

All notable changes to `llm-dup-dev` are recorded here.

The version follows [Semantic Versioning](https://semver.org). It is the
**consumer-contract** signal — the JSON schemas in `schemas/` plus the public
API the secure companion repo imports — not a per-commit identifier. Exact code
provenance lives in the `dev_git_commit` field of each manifest; this file and
the version number carry the *meaning* of a change. See `agents.md` §
Conventions → Versioning for the bump rules.

From 1.0.0 on: a breaking schema/API change bumps the **major**, an additive
backward-compatible change bumps the **minor**, and a fix — including a
behaviour fix that moves no schema or API shape — bumps the **patch**. Entries
below `1.0.0` follow the older pre-1.0 rule, where a breaking change bumped the
minor and everything else the patch.

**A release is not a study.** The version tracks compatibility only. The version
behind a given published analysis is identified by a git tag and an archived
snapshot, not by a round version number.

## [Unreleased]

## [1.0.0] - 2026-11-13

### The schema and API contract is now declared stable

**No code changed.** `git diff v0.9.3..v1.0.0 -- src/` is empty: this release is
the 0.9.3 tree with packaging metadata and documentation brought up to the
standard a stable, publicly installable release needs.

1.0.0 is cut because a full study cycle — the four `study_eval` steps of the
performance evaluation — completed without the contract moving underneath it.
That is the evidence the contract is stable; the version number now says so.

**Provenance of the performance-evaluation study.** Its artifacts record
`dev_package.version` `0.9.2` for steps 1–3 and `0.9.3` for step 4 (`finalize`).
Those are the versions that ran, and 1.0.0 deliberately appears in no artifact.
The entire 0.9.2 → 0.9.3 code difference is contained in `read_arbitration`
(`study_eval/scle/arbitration.py`), whose only caller is `finalize`; steps 1–3
have no call path to it. Re-running the whole study under 0.9.3 or 1.0.0 would
therefore reproduce the same results.

- **`umc-research` and `beautifulsoup4` are removed from the dependencies.**
  Neither was imported anywhere in `src/`, `tests/`, or `scripts/` — both were
  left over from the legacy `lib/` modules deleted earlier. `umc-research` is
  proprietary and resolvable only from an internal index, so its removal is
  what makes the package installable outside UMC. Consumers that use
  `umc-research` themselves must declare it directly; the secure repo already
  does, with its own `[tool.uv.sources]` index mapping.
- **`openpyxl` moves from the `study` extra to a core dependency, and the
  extra is removed.** Four `study_eval` modules import it at module level, and
  `study_eval` is a documented entry point, so `pip install llm-dup-dev`
  followed by `docs/study_eval.md` used to end in an `ImportError`. The extra
  had no consumer: the secure repo installed plain `llm_dup_dev` and declared
  `openpyxl` directly. `llm-dup-dev[study]` still resolves for anyone who has
  it written down — it is now a no-op rather than an error.
- **`requires-python` is now `>=3.11,<3.13`**, matching what `agents.md` has
  always claimed, what the development environment uses (3.12), and what the
  secure repo enforces. The metadata previously said `>=3.11` with no upper
  bound.
- **The package is licensed**, under the Apache License 2.0, copyright 2026
  Uppsala Monitoring Centre. It had no licence before, which made it legally
  unusable by anyone outside UMC however openly it was shared. Apache-2.0 over
  a shorter permissive licence for the explicit patent grant and the explicit
  non-grant of trademark rights. `LICENSE` carries the verbatim licence text
  and `NOTICE` the copyright assertion; per-file licence headers are
  deliberately *not* added, so that the no-code-changed invariant above holds
  at the `v1.0.0` tag.
- **The method is named vigiTwin**, and the repository is published as
  <https://github.com/Uppsala-Monitoring-Centre/vigiTwin>. The **Python package
  is deliberately not renamed** in this release: `DevPackageInfo.name` is a
  default baked into `characterization_report.schema.json` and
  `study_evaluation_run_manifest.schema.json`, the module path appears in
  generated schema descriptions across five more schema files, and every import
  in the companion repository would break. That is a contract change on three
  fronts, so it belongs to 2.0.0 with a migration note — alongside the
  regulator-pilot work already expected to be breaking. Until then the
  repository is `vigiTwin` and the import is `llm_dup_dev`, and the artifacts
  recording `dev_package.name: "llm-dup-dev"` continue to match the releases
  that produced them.
- **`docs/integration_guide.md` § Public surface** enumerates the contract for
  the first time. Previously it was described as "the public API the secure
  repo imports", which named no actual set. Everything not listed there is
  internal and may change in any release — that boundary is what keeps future
  major bumps affordable.
- **`agents.md` § Conventions → Versioning** gains the post-1.0 bump rules, an
  explicit no-deprecation-window policy, and the separation between releases
  and study cycles. § Repository Structure has been rewritten — it described a
  layout with five "(planned)" packages that shipped long ago and two legacy
  packages that no longer exist. § Dependencies now records that no
  proprietary or internal-index dependency may be added to this package.
- **`README.md` was empty and is now written**, aimed at a reader arriving from
  the publication: what the method does, what can be run without database
  access, and how the two repositories divide.

## [0.9.3] - 2026-08-15

### Fixed — a blank feature-domain arbitration cell is a decision, not a gap

`read_arbitration` dropped every blank consensus cell, which is right for the
dimensions that carry a single value and wrong for the one that carries a set.
"No feature domains are implicated" is a real arbitration outcome, and the
workbook has no way to render it other than an empty cell. Read-back therefore
reported the decision as missing, and `merge_consensus` refused to finalize a
pair the arbiter had in fact resolved.

Encountered during finalization of the frozen SCLE evaluation: reviewer A
implicated no domains, reviewer B implicated one, and the arbiter sided with A.

- **A blank but applicable `implicated_feature_domains` consensus cell is now
  retained** as an explicit empty value, so the consensus resolves to
  `implicated_feature_domains == []` with
  `dimension_sources["implicated_feature_domains"] == "arbitration"`.
- **Blank stays "undecided" everywhere else.** Every other disagreed structured
  dimension still fails validation when its consensus cell is empty — this is
  not a general relaxation, and is pinned by a test.
- **A dimension that never applied to the pair is still not an answer.** An
  `N/A` cell, and an absent column in an older workbook, are both dropped as
  before, so no empty decision is invented for a question that was never asked.

`merge_consensus` is unchanged: `_parse_domains("") -> []` already carried the
empty case correctly once the key survives read-back. No schema changed and no
public API shape changed; this is behaviour-only, and ships as a patch.

One narrow consequence worth knowing: where the two reviewers *agreed* on a
non-empty domain set and the arbiter *cleared* that pre-filled cell, read-back
now surfaces it as an edit of a do-not-edit cell and `merge_consensus` raises,
where before the clearing was silently dropped and the agreed value stood. That
matches the workbook's stated rule — agreed dimensions are resolved from the
reviews — but the error text still offers "clear the consensus cell" as a remedy,
which no longer applies to this one dimension.

## [0.9.2] - 2026-08-06

### Added — shared loaders for the two frozen artifact shapes

Driving `characterization` from a notebook against an existing frozen validation
set surfaced two papercuts, both from the same root: a reports file has two
equally-correct shapes in this project, and the pair models spell their id
fields differently. Both are now handled once, in the library, instead of by
every caller. Purely additive; no schema changed.

- **`data.offline.load_reports(path) -> {report_id: Report}`** accepts **either**
  frozen shape — a JSON array of `Report` objects (the secure runner's
  `validation_reports.json`) or an object keyed by `report_id` (what
  `review_html` writes). The tolerance stops at the shape: a duplicate id, a
  mapping key that disagrees with the record inside it, a non-object list item,
  or any other top-level type is still rejected.
- **`data.offline.index_by_id(...)`** is the generic helper underneath it,
  promoted from `review_html.saved_run._index_by_id`. It also serves pair-keyed
  artifacts (`FrozenPairBundle`), which is why it stays generic.
- **`review_html`'s `--reports` now accepts a list as well as a mapping**, and
  **`characterization`'s `--reports` now accepts a mapping as well as a list**.
  Both are widenings — every file that worked before still works.
- **`study_eval.loading.reference_set_from_report_pairs(pairs, set_id=...)`**
  builds a `ReferenceSet` from `ReportPair`s a caller already holds, handling the
  `report_1_id` → `report_id1` rename. Ids are carried across, never re-derived,
  so a noncanonical pair still fails rather than being quietly repaired.
  `load_report_pairs(path)` reads a frozen `pairs.json` for it. Both are
  re-exported from `llm_dup_dev.study_eval`.
- `role` defaults to `"recall"` there, because that is the cheapest legal set —
  no per-pair labels. Describing a set should not require first inventing a
  mapping from the annotation vocabulary (`duplicates` / `otherwise_related` /
  `unrelated`) onto the human-reference one (`duplicate` / `non_duplicate`);
  that collapse is a study decision and the helper will not make it. Conversely
  `screening_mode` keeps the model's `"observed"` default — `not_evaluated` is a
  claim about the estimand and is never inferred.

`scripts/extract_frozen_*.py` keep their own array-only loader on purpose: they
warn-and-keep-first on a duplicate `report_id` rather than raising, which suits a
batch extraction job and is a separate decision from this one.

## [0.9.1] - 2026-08-06

### Added — reference-set characteristics ("Table 1")

A new package, `llm_dup_dev.characterization`, describes **what is in a
reference set**: reporting years, countries, patient age (median/IQR and 10-year
bands) and sex, the ten commonest drugs and adverse events (PT and HLT level),
and pair-level agreement on country, reporting year and sex. Purely additive —
no existing model, schema or function changed. Full detail in
`docs/reference_set_characteristics.md`.

- **New consumer contract:** `schemas/characterization_report.schema.json` with
  `examples/characterization_report_example.json`, regenerated by
  `scripts/generate_characterization_schema.py` and drift-checked by
  `tests/test_schemas_examples.py`.
- **New public API:** `build_characterization_report` /
  `build_reference_set_characteristics` take a `ReferenceSet` plus either a
  `{report_id: Report}` mapping or a `ReportLoader`, and return an
  aggregate-only artifact containing no report content;
  `write_characterization_report` emits JSON and markdown (CSV on request).
- **New command:** `python -m llm_dup_dev.characterization`, reading the same
  `study_reference_set.schema.json` files `study_eval analyze` reads.
- **`study_eval` is untouched and does not import it.** It never handles
  `Report` objects, so this runs before it as its own command rather than as a
  subcommand or a `run_analysis` parameter; a test enforces that nothing in
  `llm_dup_dev` imports the new package.
- **The reduction rule is recorded in the artifact.** Reports carry several
  candidate values per demographic, so tabulation needs a documented rule:
  structured sources win, and disagreement that survives canonicalisation is
  counted as `conflicting` rather than silently resolved. `policy` travels with
  the numbers, for the same reason `EvaluationRunManifest` records
  `analysis_positive_labels` beside the variant name.
- **Missingness is a category, never a dropped row.** Every distribution's
  dispositions partition its denominator, and `unavailable_reason`
  (`missing_drug_role`, `missing_hierarchy`) separates "this question could not
  be asked of this data" from "the answer is empty".
- **No small-cell suppression, by design.** Counts are exact; whether the
  artifact may leave the secure environment is a human decision made with the
  numbers in view. A test pins the absence of suppression.

## [0.9.0] - 2026-08-05

The breaking changes below shipped together. `0.9.0` has been exercised
secure-side, so it now names this contract and nothing else — the additive
characterization package above is `0.9.1` rather than a re-cut of this number,
for the same reason `0.7.0` could not be reused (see below).

### 0.7.0 was retired, not released

The version was bumped to 0.7.0 for the SCLE form simplification, and a
secure-side dry run of the SCLE workbooks and orchestration then ran against it.
That run baked `0.7.0` into durable artifacts — `dev_package.version` in its
`evaluation_run_manifest.json`, and `package_version` in every `SamplingRecord`
inside its `scle_sample_*.json`, which is the file reviewer workbooks checksum
against. So 0.7.0 already denotes an exercised contract: the simplified form
with the **previous** workbook layout.

Reusing the number for the new layout would leave `dev_package.version: 0.7.0`
meaning two different workbook contracts. The next release is therefore 0.8.0,
and 0.7.0 is spent — it names the dry-run contract and nothing else. There is
deliberately no `[0.7.0]` heading below; artifacts carrying that version are
identified by their `dev_git_commit`.

### Breaking — SCLE form simplified and partly study-configured

The study team refined the Structured Case-Level Examination questions. Full
detail, including what the secure side must do, is in
`docs/migration_notes_scle_form_simplification.md`; the aggregate performance
analysis (Step 1) is untouched.

- **Case difficulty gained a `clear` category** between `trivial` and
  `non_trivial`. Duplicate identification is inherently ambiguous, so `trivial`
  is now reserved for pairs where nothing is left to judge; `clear` covers pairs
  whose correct label is obvious to a human but would still require reliable
  extraction by a computational method.
- **`never_event` and `unexpected_method_behaviour` are strictly `yes`/`no`.**
  `YesNoUnclear` is replaced by `YesNo`; existing `unclear` answers are no
  longer expressible.
- **`material_input_data_limitation` and `input_data_limitation_category` are
  replaced by one binary `incorrect_or_imprecise_input_data`** — the only
  input-data problem the team expects to identify reliably. The
  `InputDataLimitationCategory` enum is removed.
- **`primary_error_source` is replaced by a free-text `error_source_description`
  plus one tick-only flag per method stage.** Misclassifications usually involve
  an interaction between components, which a single-choice enum could not
  express. The `PrimaryErrorSource` enum is removed.
- **The stage list and the feature-domain list are now study configuration that
  generates columns.** `generate_reviewer_workbooks` takes `error_stages`
  alongside `feature_domains`; each entry becomes one tick-only yes/no column,
  and each list is embedded in the workbook so every later step recovers it from
  the artifact. The package-level `FEATURE_DOMAINS` default is removed —
  **omitting a list now drops that question from the examination**.
- **The structured-dimension set is no longer fixed.** Each configured stage is
  its own dimension (`error_stage::<stage>`) with its own kappa and arbiter
  consensus column; feature domains stay one set-valued dimension.
  `DiscrepancyReport` and `ArbitrationSubmission` gained `structured_dimensions`;
  `ReviewerSubmission` gained `error_stages` and `feature_domains`.
- **Tick-only answering changes an agreement denominator.** A blank cell means
  "no", so two reviewers who both ticked nothing on a tick-only dimension now
  count as agreeing rather than being skipped as inapplicable.
- **Every question carries its interpretation inside the workbook** — a comment
  on its header cell plus a `guidance` sheet.
- Module `scle/feature_domains.py` became `scle/vocabularies.py`;
  `augment_reviewer_workbook` and `validate_submission_feature_domains` are
  removed (an out-of-vocabulary domain is no longer expressible). The
  `study_eval` CLI's `workbooks` subcommand gained repeatable `--error-stage`
  and `--feature-domain` flags.

### Breaking — SCLE workbooks laid out for surveying

The reviewer and arbitration workbooks are the surface two co-authors work for
hundreds of pairs, so the pair sheet was rebuilt to fit on a screen. Full detail
is in `docs/migration_notes_scle_workbook_layout.md`. No model field, JSON
schema or Python identifier changed — this is the spreadsheet surface only —
but a workbook written by an earlier version no longer reads back.

- **The `cases` sheet is now `pairs`,** matching the entities of the reference
  sets; the metadata key `n_cases` is now `n_pairs`, and the reviewer-facing
  wording says "pair" throughout.
- **The `not_applicable` sentinel is now `N/A`** (plain text, not Excel's `#N/A`
  error value), and `Case difficulty` is now `Difficulty`.
- **The generated column prefixes are `S: ` and `F: `** instead of
  `Stage contributing: ` and `Implicated: `. The `instructions` and `guidance`
  sheets spell out what each letter means.
- **The pair id links to the pair's HTML packet,** so the separate `Packet`
  column is gone.
- **Auto-context columns are chosen per sample.** A column holding one value for
  the whole workbook — the component, the reference set of a single-set sample,
  the screening verdict on a precision set — is stated once on `metadata`
  instead; a column no sampled pair populates is dropped. `Misclassified` and
  the rest are never dropped for merely looking uniform in a small draw.
- **The pair sheet is formatted**: every cell top-aligned, labels centred, prose
  columns left-aligned and wrapped, per-column widths, and panes frozen below
  the header and right of the pair id.
- `read_reviewer_submission` now rejects a pair sheet whose `Pair ID` column has
  been renamed away, instead of reporting every row as missing.

### Breaking — the analysis variant is study configuration, not a dev-side enum

`AnalysisVariant` is removed. Which label collapses a study runs, what they are
called, and which one is the baseline are the study's prespecified choices, so the
package now owns only the adjudicator's output vocabulary
(`ADJUDICATOR_LABELS`) and the mechanism: a `PositiveClass(name,
positive_labels)` whose labels are validated as a non-empty subset of it. This is
the division `scle/vocabularies.py` already applies to the SCLE vocabularies.

The fixed enum could not express the protocol's own secondary analysis 2
("*Abstain* **and** *Otherwise related* positive"): `OTHERWISE_RELATED_POSITIVE`
is `{Duplicates, Otherwise_related}`, so that analysis was unrunnable without a
dev-side code change.

Full detail, including what the secure side must do and the checks to run before
the frozen analysis, is in `docs/migration_notes_positive_class.md`.

- **`run_analysis`, `run_precision_analysis` and `run_recall_analysis` take
  `positive_class: PositiveClass`** in place of `variant`, defaulting to `PRIMARY`.
  `PRIMARY`, `ABSTAIN_POSITIVE` and `OTHERWISE_RELATED_POSITIVE` remain as
  convenience constants for this study; a study may define its own instead.
- **`run_precision_variants` / `run_recall_variants` require an explicit
  `variants` sequence** and return dicts keyed by rule name. The
  `REINTERPRETATION_VARIANTS` tuple is removed — which rules to run is not a
  package default. Empty lists and repeated names are rejected.
- **The baseline is named explicitly.** `write_study_artifacts`,
  `write_secondary_comparison_csv` and `summary_to_markdown` take `baseline=<rule
  name>`, required wherever results are supplied. Both writers previously found
  the baseline by matching the literal string `"primary"` and, finding nothing,
  emitted a header-only `secondary_comparison.csv` and a title-only `summary.md`
  — silently, and reachable today by any run under a non-primary variant. A
  missing baseline now raises.
- **`secondary_comparison.csv` is written only when the results span two or more
  rules**, so a single-rule run no longer produces an empty comparison file. Its
  `primary_proportion` column is renamed `baseline_proportion`, and `summary.md`
  headings name the baseline rule rather than saying "primary".
- **The manifest records the rule's labels alongside its name.**
  `EvaluationRunManifest` gains `analysis_positive_labels`; because rule names are
  study-supplied, `analysis_variant` alone no longer identifies what was counted
  as positive. Schema and example regenerated.
- The CLI keeps `--variant` over the three named rules and gains a repeatable
  `--positive-label` for defining an ad-hoc rule.

### Fixed — SCLE auto-context could contradict its own stratum

`sample_scle_precision` and `sample_scle_recall` derived `methods_disagree`,
`is_misclassified` and `confusion_cell` under the **primary** rule regardless of
the rule the analysis actually ran under, while taking the strata from the
variant-aware result. Under any non-primary rule the two disagreed: a recall pair
whose adjudicator label was `Abstain` landed in the `both` stratum — positive
under `ABSTAIN_POSITIVE` — while being flagged `is_misclassified=True`. Since
`methods_disagree` and `is_misclassified` gate which conditional dimensions a
reviewer is asked, a reviewer would have been asked the wrong questions.

Both samplers now take a required `positive_class` and use it, so a sample is
internally consistent whichever rule produced it. `run_analysis` forwards the rule
in force.

Consequently **SCLE sampling and the collapse rule are now independent**, and the
warning that fired when `sample_scle=True` met a non-primary variant is gone: it
claimed the strata reflected that variant's rule, which was only half true, and
examining cases under a secondary rule is a legitimate study configuration.
Running the primary rule aggregate-only was, and remains, equally supported.

### Changed — an empty reference-set bundle is now an input error

`preflight_reference_sets` (and so `run_analysis`) rejects a bundle with no
reference sets. It previously returned cleanly, writing a manifest, an empty
`predictions_table.csv` and an estimate-free `study_results.json` — a run that
measured nothing was indistinguishable from a run that succeeded. Any bundle
holding at least one set is unaffected.

### Fixed — `secondary_comparison.csv` mis-attributed precision rows

Every precision row was labelled with the **first** primary result's `set_id`
and differenced against **that** set's baseline, whichever set the variant
result actually came from. A bundle pooling two precision sets across the
re-interpretation variants therefore filed half its rows under the wrong set,
against the wrong baseline: a row reading `prec_a, adjudicator_precision,
abstain_positive, 0.5714, 0.6667, 0.0952` was `prec_b`'s result, whose true
delta against its own 0.6667 baseline is `0.0000`. Recall rows were already
keyed per set and were never affected.

Precision now keys its baselines by `set_id` as recall does, and each row names
the set its figure came from. A variant result whose set has no primary result
in the bundle now raises `KeyError` rather than silently borrowing another
set's baseline.

The table is only populated by a caller that assembles several variants into
one `write_study_artifacts` call — via `run_precision_variants` /
`run_recall_variants` — so a study following the documented one-variant-per-run
workflow has an empty comparison table and no affected figures. Any consumer
that built one by pooling **more than one precision set** should regenerate it.

### Documentation and tests

- **Single-role bundles are supported, and now pinned.** A precision-only or a
  recall-only study runs through all four steps: the absent role's estimands,
  result tables and SCLE sample are not produced, and `summary.md` carries only
  the section that applies — an absent table is never a table of zeros. This
  already worked, but every workflow test used a both-roles bundle, so nothing
  stopped a future validator from quietly taking it away.
  `tests/test_study_eval/test_workflow.py` now covers each role alone
  (including steps 2–4 on a recall-only sample), and `docs/study_eval.md` §
  Workflow states it.

- Pinned and documented the exact scope of what a canonical `pair_id`
  guarantees about `PairFeatures` content. `generate_pair_features` derives a
  canonical id but leaves `r1` / `r2` in the caller's order, so a reversed
  direct call returns a payload differing in presentation under the same
  `pair_id` — `evidence_summary.report1_only` / `report2_only` swap, and
  descriptions quoting both sides quote them in the other order. Everything
  that scores, joins or decides is invariant, including the whole
  `ClassificationResult`.

  That invariance was emergent rather than enforced.
  `tests/test_pipeline.py::TestOrientationInvariance` now asserts it across the
  whole payload except those two exceptions, so a future orientation-dependent
  rule fails loudly instead of making one `pair_id` mean two different answers.
  No behaviour change; see `docs/data_contracts.md` § Canonical pair identity.

## [0.6.1] - 2026-08-01

Reviewers comparing an adjudication against the screening method that surfaced
the pair can now see what that method computed. A new optional artifact,
`ScreeningDetails`, carries the per-pair basis for the **screening**
algorithm's classification, and `review_html` renders it as a Screening
Algorithm section directly beneath Classification on each pair page. Nothing in
feature generation or the decision logic reads it — it is presentation only.

### Added

- `llm_dup_dev.data.offline.ScreeningDetails`, with `save_screening_details` /
  `load_screening_details`. The artifact is keyed by `pair_id`, optionally
  wrapped with the algorithm's name:
  `{"algorithm": "vigiMatch", "entries": {"<pair_id>": {"<feature>": <value>}}}`.
  The bare `{pair_id: {feature: value}}` form is accepted too and normalised on
  load, leaving `algorithm` unset.

  Screening algorithms vary, so the per-pair payload is deliberately **untyped
  at the feature level**: any set of `feature -> value` pairs validates, keys
  render verbatim (they are the algorithm's own identifiers), and key insertion
  order is the display order. Values must be scalars or flat lists of scalars;
  a nested object is rejected at load, since there is no sensible rendering for
  one in a two-column table. Coverage may be partial — pairs absent from
  `entries` render with no section at all.
  Schema: `schemas/screening_details.schema.json`.

- Pass-through on every `review_html` entry point: `--screening` on the CLI,
  `screening_path=` on `load_saved_run` / `render_saved_run` (whose canonical
  pair-key check now covers it, so `allow_noncanonical_pairs` governs it like
  every other pair-keyed artifact), `screening=` / `screening_algorithm=` on
  `PairRenderInput`, `ResultRenderInput`, `build_pair_view_model` and the
  `render_*_html` functions, and `screening=` on
  `study_eval.scle.packets.generate_packets` for SCLE reviewer packets.

  On the SCLE path this is a `generate_packets` parameter rather than a
  `PacketContent` field: the `PacketContentProvider` seam exists to keep
  confidential *report content* out of the generic schemas, and screening
  details are already a generic pair-keyed artifact the caller can load whole.

  Display formatting is fixed by the renderer so pages stay comparable across
  algorithms: floats at two decimals, extended only far enough to reach a
  significant digit (`0.0034` → `0.003`); booleans as Yes/No; nulls and empty
  lists as an em dash.

All additions are optional and backward compatible — existing callers,
artifacts and rendered pages are unaffected.

## [0.6.0] - 2026-07-31

Canonical pair identity: a report pair now has **one** identity regardless of
the order its two report ids are supplied in. The ids are stored in ascending
canonical order — numerically for numeric ids, so `9` precedes `10` — and
`pair_id` is always `{report_1_id}__{report_2_id}` derived from that order.

Previously nothing sorted the two ids, so `A__B` and `B__A` were different ids
for the same two reports. Because `PairFeatures` and `ClassificationResult`
carry only a `pair_id` and no report ids, that id is the sole join key between
features, classifications, annotations, reference sets and predictions — and a
flipped orientation silently scored zero pairs while still reporting a number.

Minor rather than patch: `ReportPair` and `study_eval.ReferencePair` now reject
input they previously accepted, and `eval.sweep.Coverage` loses a field.

See `docs/migration_notes_canonical_pair_identity.md` for the secure-side
migration, including which artifacts must be regenerated.

### Added

- `llm_dup_dev.models.pair_identity` (re-exported from `llm_dup_dev.models`):
  `canonical_pair_id`, `canonical_report_ids`, `report_id_sort_key`,
  `split_pair_id`, `validate_pair_identity`, `validate_report_id`,
  `PAIR_ID_SEPARATOR`.
- `ReportPair.from_report_ids(a, b)` and `ReportPair.from_reports(r1, r2)`,
  which canonicalise. These are the supported way to build a pair.
- `allow_noncanonical_pairs` on `load_saved_run` / `render_saved_run` and
  `--allow-noncanonical-pairs` on the review-HTML CLI: an escape hatch for
  *rendering* a run saved before canonicalisation. Not offered on the
  `study_eval` path, where a noncanonical set must be fixed at source.

### Changed (breaking)

- `ReportPair` validates its identity on construction and on `model_validate`.
  Noncanonical report order, a `pair_id` inconsistent with the ids, a self-pair,
  a blank id, and an id containing `__` are all rejected. Records are **not**
  silently rewritten: `pair_id` is a join key, so a rewrite would break the
  joins the invariant protects. `schemas/report_pair.schema.json` regenerated.
- `study_eval.ReferencePair` enforces the same invariant;
  `schemas/study_reference_set.schema.json` regenerated. Its separate
  unordered-pair check is gone — with canonical ids, two rows naming the same
  two reports collide on `pair_id` and are caught as a duplicate.
- `eval.annotations.derive_pair_id` is now canonical. Same name and signature;
  the returned id no longer depends on argument order. An annotation file
  listing a pair the other way round from the run therefore joins correctly, and
  the same two reports listed twice in opposite orientations now fails at load.
  A row's own `report_id1` / `report_id2` keep file order as provenance.
- `feature_generation.generate_pair_features` derives a canonical `pair_id` when
  none is passed. It does **not** swap `r1`/`r2`: feature content is
  orientation-dependent, and the roles are canonicalised upstream at `ReportPair`
  construction.
- `eval.sweep.Coverage.reversed_orientation` and `eval.sweep._reverse_pair_id`
  removed, along with the corresponding warning in `eval.report`. With both
  sides canonical a reversed annotation file is no longer possible; a *features*
  file predating canonicalisation now shows up under `unmatched_annotations`.
- `load_saved_run` rejects pair-keyed artifacts (`pair_results`, `features`,
  `classifications`, `bundles`) whose keys are pre-canonical pair ids. Keys that
  are not two report ids joined by `__` are left alone — `pair_id` remains
  free-form on `PairFeatures` and `ClassificationResult`.

### Migration

- Regenerate any `pairs.json`, `features.json`, `classifications.json`,
  `pair_results.json`, frozen bundle file, reference set, prediction set, SCLE
  packet or review HTML whose `pair_id` is not canonical.
- Features and classification artifacts carry no report ids, so their contents
  cannot be validated — regenerate them whenever their `pairs.json` fails.
- A stale bundle file surfaces as `FrozenStatsProvider`'s existing
  `No frozen bundle found for pair_id ...`.
- Review-HTML left/right column order flips for pairs previously supplied as
  (candidate, match) where the candidate sorts second.
- Self-pairs are now invalid; a report-against-itself sanity control must use
  two distinct report ids.
- Report ids must be `str` — pydantic does not coerce `int` from SQL.

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
