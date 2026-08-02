# Migration notes: SCLE form simplification

**Audience:** the secure-side deployment (`llm_dup_pv_secure`) and any tooling
that reads SCLE reviewer submissions, consensus records, or the generated
schemas. This document describes the contract changes only; no secure-side code
is modified from this repository.

## Summary

The study team simplified the Structured Case-Level Examination form after
finding several dimensions hard to use in practice. The reviewer questions
changed, two enums were removed, one lost a member and one gained one, and the
set of structured dimensions is now **partly determined by study configuration**
rather than fixed by this package.

Nothing in the aggregate performance analysis (Step 1) is affected. The changes
are confined to Steps 2–4 (workbooks, arbitration, consensus).

## Changes to the reviewer form

| Before | After |
|---|---|
| `case_difficulty`: `trivial`, `non_trivial`, `unclear` | adds `clear` between `trivial` and `non_trivial` |
| `never_event`, `unexpected_method_behaviour`: `yes`/`no`/`unclear` | `yes`/`no` — the `unclear` option is gone |
| `material_input_data_limitation` + `input_data_limitation_category` | one binary `incorrect_or_imprecise_input_data` |
| `primary_error_source` (single enum) | free-text `error_source_description` + one tick-only flag per configured stage |
| `implicated_feature_domains` as one comma-separated cell | one tick-only yes/no column per configured domain |

The `clear` category exists because duplicate identification is inherently
ambiguous: `trivial` is now reserved for pairs where nothing is left to judge,
while `clear` covers pairs whose correct label is obvious to a human but would
still require reliable extraction by a computational method.

Removed enums: `InputDataLimitationCategory`, `PrimaryErrorSource`. Renamed
enum: `YesNoUnclear` → `YesNo` (and it no longer carries `unclear`).

Every question now carries a brief interpretation inside the workbook: a comment
on its header cell in the `cases` sheet, plus a `guidance` sheet listing all of
them.

## Configuration: the study now supplies two vocabularies

`generate_reviewer_workbooks` takes two frozen ordered lists:

- `error_stages` — the method components a reviewer may flag as contributing to
  a misclassification. Previously the three components were hard-coded in this
  package as members of `PrimaryErrorSource`; they are now study configuration,
  so the study's YAML should carry them (for this study:
  `evidence_extraction`, `independence_assessment`, `relationship_assessment`).
- `feature_domains` — unchanged in meaning, but now column-generating.

**Each entry becomes one tick-only yes/no column**, so the two lists determine
part of the workbook's column set. Consequences:

- **Omitting a list drops that question from the examination entirely.** There
  is no package-level default to fall back on — the `FEATURE_DOMAINS` constant
  in `scle/form.py` has been removed. An accidental omission is a silent scope
  change, so both lists belong in the study checklist.
- Each list is embedded in a worksheet of its own, so a workbook describes its
  own columns. `read_reviewer_submission`, agreement, discrepancy detection,
  arbitration, and consensus all recover the vocabularies from the artifacts;
  passing them to `build_arbitration` / `finalize` is now an optional
  cross-check rather than the validation path.
- Tick-only means a blank cell is a valid answer meaning "no". Two reviewers who
  both ticked nothing now count as agreeing on that dimension rather than being
  skipped, which changes the denominators of the affected agreement statistics.

## Structured dimensions are no longer a fixed list

Each configured error stage is its own structured dimension, keyed
`error_stage::<stage>`, with its own Cohen's kappa and its own arbiter consensus
column. The feature domains remain a single set-valued dimension.

`DiscrepancyReport` and `ArbitrationSubmission` gained a
`structured_dimensions` field recording the set actually used, and
`ReviewerSubmission` gained `error_stages` and `feature_domains`. The
`scle_consensus_*.csv` and `scle_dimension_frequencies_*.csv` tables therefore
gain one column (or row group) per configured stage.

## Code-facing renames (for spec-level alignment on the secure side)

- Module `scle/feature_domains.py` → `scle/vocabularies.py`, generalised over
  both lists. `normalise_feature_domains` → `normalise_vocabulary(values, *,
  label=...)`; `workbook_feature_domains` → `workbook_vocabularies` (now in
  `scle/workbook.py`, returning `(error_stages, feature_domains)`).
- `augment_reviewer_workbook` is **removed**. It applied the domain list to an
  already-written workbook, which is impossible now that the list determines the
  columns; `write_reviewer_workbook` takes the lists directly.
- `validate_submission_feature_domains` is **removed**. An out-of-vocabulary
  domain is no longer expressible — reviewers tick columns rather than typing
  names — so read-back validation subsumes it.
- `study_eval` CLI: the `workbooks` subcommand gained repeatable
  `--error-stage` and `--feature-domain` flags. Without them the CLI produces
  workbooks with neither question group.

## What the secure side needs to do

1. Add an `error_stages` list to the study's YAML alongside `feature_domains`,
   and pass both to `generate_reviewer_workbooks`.
2. Upgrade the installed `llm-dup-dev` to 0.7.0.
3. Regenerate any reviewer workbook already issued. Read-back of a workbook
   generated by an earlier version will fail validation — the column set and the
   `case_difficulty` vocabulary both changed.
4. Re-review any cases already examined under the old form. The answers cannot
   be migrated: `primary_error_source` has no successor field, and
   `never_event` / `unexpected_method_behaviour` answers of `unclear` are no
   longer expressible.
