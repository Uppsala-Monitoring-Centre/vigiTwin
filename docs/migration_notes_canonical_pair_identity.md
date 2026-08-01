# Migration notes: canonical pair identity

**Audience:** consumers of serialized pipeline output — in particular the
secure-side deployment (`llm_dup_pv_secure`) and any tooling that builds report
pairs, reads `pairs.json` / `features.json` / `classifications.json`, or
constructs reference sets. This document describes the contract changes only;
no secure-side code is modified from this repository.

## Summary

A report pair now has **one** identity, independent of the order its two report
ids are supplied in:

1. `report_1_id` and `report_2_id` are stored in **ascending canonical order**.
2. `pair_id` is always `{report_1_id}__{report_2_id}` derived from that order.
3. Numeric ids are ordered **numerically** — `9` precedes `10`. Non-numeric ids
   are ordered lexicographically, and numeric ids sort before non-numeric ones.
4. A report may not be paired with itself, and a report id may not be blank or
   contain the `__` separator.

So `(A, B)` and `(B, A)` are the same pair with the same `pair_id`.

Previously `pair_id` was `f"{report_1_id}__{report_2_id}"` with nothing sorting
the two ids, so `A__B` and `B__A` were different ids for the same two reports.
Because `PairFeatures` and `ClassificationResult` carry only a `pair_id` and no
report ids, `pair_id` is the *sole* join key between stored features,
classifications, gold annotations, reference sets and frozen predictions — and a
flipped orientation silently scored zero pairs while still reporting a number.

## The dev-side API to use

All in `llm_dup_dev.models.pair_identity` (re-exported from
`llm_dup_dev.models`):

| Function | Use it when |
|---|---|
| `canonical_pair_id(a, b)` | Building a `pair_id` for a new pair. |
| `canonical_report_ids(a, b)` | You need the two ids in canonical order. |
| `validate_pair_identity(pair_id, r1, r2, *, context="")` | Reading a pair back from an artifact someone else wrote. |
| `split_pair_id(pair_id)` | Recovering the two ids from a canonical `pair_id`. |
| `report_id_sort_key(report_id)` | Sorting report ids yourself. |

And on the model:

| Constructor | Use it when |
|---|---|
| `ReportPair.from_report_ids(a, b)` | Building a pair from two ids, in any order. |
| `ReportPair.from_reports(r1, r2)` | Building a pair from two `Report` objects. |

`ReportPair` and `study_eval.ReferencePair` both validate the invariant on
construction *and* on `model_validate`, so a noncanonical record from an
artifact raises rather than being accepted.

**The ordering rule is frozen.** Changing `report_id_sort_key` would silently
re-key every stored pair and break every join against previously written
artifacts.

## Construct with the helpers; validate — never normalise — existing artifacts

This is the distinction that matters for every entry point below.

- At **construction** time, canonicalise. Never build a `pair_id` with an
  f-string: `f"{a}__{b}"` puts `"10"` before `"9"`, and it will not agree with
  what the pipeline derives.
- At **read** time, on an artifact that already exists, call
  `validate_pair_identity(...)` and **fail**. Do not reorder the ids or rewrite
  the `pair_id` in place. That id is the join key against annotations and
  predictions that are still keyed on the old value, so a silent rewrite breaks
  exactly the joins the invariant exists to protect. The supported repair is to
  regenerate the artifact.

Report ids must be **strings**: pydantic v2 does not coerce `int` to `str`, so
numeric ids coming out of SQL need an explicit `str()` before they reach a model.

## Secure-side entry points

### XLSX converters

- Where a pair is *built* from two spreadsheet columns, use
  `ReportPair.from_report_ids(str(a), str(b))` or `canonical_pair_id(str(a),
  str(b))`. Do not assume the sheet's column order is the pair's order — the
  point of the change is that it no longer needs to be.
- Where a workbook is *read back* and joined against an artifact that already
  exists, call `validate_pair_identity(...)` with the workbook path as `context`
  and fail the conversion on a mismatch.
- The reader functions remain the source of truth (Excel protection is
  advisory), so this validation belongs in the reader, not in the sheet.

### Validation-dataset notebook

- Construct pairs with `ReportPair.from_report_ids`.
- When re-reading a previously frozen `pairs.json`, `features.json` or
  `classifications.json`, validate and stop on a mismatch. Regeneration, not
  in-place repair, is the supported migration.
- To *view* an old run without regenerating it, the review-HTML path has an
  explicit escape hatch — see "Rendering an old run" below.

### Dataset builder

- Canonicalise at pair-construction time so the dataset and its manifest agree.
- Validate, never normalise, when incorporating a pre-existing pair list.
- **Deduplicate on the canonical `pair_id`.** Pairs that were previously
  distinct as `A__B` and `B__A` now collapse into one, so a rebuilt dataset may
  legitimately contain fewer pairs than its predecessor. That is a correction,
  not data loss — but it should be noted in the dataset manifest rather than
  discovered later as an unexplained count change.

### Reference sets

Reordering a reference-set pair changes its `pair_id`. Any human annotation,
adjudication or prediction keyed on the old id must be re-keyed in the same
step, or the reference set will no longer join against it.

## Artifacts that need regenerating

Any artifact whose `pair_id` is not canonical:

- `pairs.json` — rejected on load. This is the one that fails loudly.
- `features.json`, `classifications.json`, `pair_results.json`, frozen bundle
  files — these carry **no report ids**, so a stale key cannot be validated from
  the contents. The review-HTML loader checks their *keys* for canonical
  ordering and rejects a legacy artifact; elsewhere, a stale key simply fails to
  join. Regenerate them whenever the corresponding `pairs.json` fails.
- Reference sets and prediction sets — rejected on load, with no escape hatch:
  they feed the actual estimates.
- SCLE packets and review HTML — filenames derive from `pair_id`, so they change
  with it.

Two symptoms worth recognising:

- `FrozenStatsProvider` raises `No frozen bundle found for pair_id ...` when a
  bundle file predates canonicalisation. That is a clean failure, not a bug.
- `eval.sweep.check_coverage` reports a legacy features file's pairs under
  `unmatched_annotations`. The previous `reversed_orientation` diagnostic has
  been removed — with both annotation and pipeline sides canonical, a reversed
  annotation file is no longer possible, and the remaining cause is a stale
  features file.

## Rendering an old run

`load_saved_run` / `render_saved_run` accept `allow_noncanonical_pairs=True`,
and the review CLI accepts `--allow-noncanonical-pairs`. This relaxes the
ordering and derivation checks so a pre-canonicalisation run can be viewed as
written; blank report ids and self-pairs stay rejected. Rendering is read-only
presentation — the hatch is deliberately **not** available on the `study_eval`
path, where a noncanonical set must be fixed at source.

## The staged reference files

`_reference_secure_side/run_validation_batch.py` and
`render_validation_review.py` are unchanged. Both already load pairs with
`ReportPair.model_validate(...)`, which is the correct read-path behaviour — they
will now reject a pre-canonicalisation `pairs.json` rather than accept it
silently. Two things to decide when you re-copy them:

- `run_validation_batch.py` should keep failing. It drives a run whose output is
  joined against annotations later.
- `render_validation_review.py` is pure rendering, so it is a reasonable place to
  thread `allow_noncanonical_pair_identity` through if you need to re-render an
  old batch. The dev-side review-HTML path already exposes that hatch and is the
  pattern to copy.

## Behaviour changes to expect

- **Review-HTML column order flips** for pairs the secure side previously
  supplied as (candidate, match) where the candidate sorts second. Report 1 is
  now the lower-ordered id. Feature *content* is unchanged: `generate_pair_features`
  derives a canonical id but does not swap `r1`/`r2`, so a run driven by
  canonical `ReportPair` objects gets both consistently.
- **Self-pairs are now invalid**, so a report-against-itself sanity control is no
  longer expressible. Use two distinct report ids.
- **`eval.sweep.Coverage` lost its `reversed_orientation` field**, and
  `eval.sweep._reverse_pair_id` is gone.
- **`llm_dup_dev.eval.annotations.derive_pair_id` is now canonical.** The name
  and signature are unchanged; the returned id no longer depends on argument
  order.
