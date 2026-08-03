# Migration notes: SCLE workbook layout

**Audience:** the secure-side deployment (`llm_dup_pv_secure`) and any tooling
that opens, fills, or parses SCLE reviewer or arbitration workbooks. This
document describes the contract changes only; no secure-side code is modified
from this repository.

## Summary

The reviewer and arbitration workbooks are the surface two co-authors work for
hundreds of pairs, so the sheet they spend their time on was rebuilt to fit on a
screen: fewer columns, shorter labels, and formatting that lets a row be read at
a glance.

**Nothing in the data contract changed.** No model field, JSON schema, enum
value, or Python identifier is affected — `SCLECaseAssessment`,
`case_difficulty`, `sample.cases` and `schemas/*.schema.json` are all as they
were, so a stored `ReviewerSubmission` or consensus record from before this
change is still valid. What changed is what the spreadsheet looks like and what
the read-back expects to find in it.

**A workbook written by an earlier version does not read back.** The sheet
rename alone breaks it, so no partial tolerance is offered. Any workbook already
issued to a reviewer must be regenerated from its sample, or filled and read
with the previous package version.

## Changes to the workbooks

| Before | After |
|---|---|
| sheet `cases` | sheet `pairs` |
| metadata key `n_cases` | `n_pairs` |
| cell sentinel `not_applicable` | `N/A` |
| header `Case difficulty` | `Difficulty` |
| header prefix `Stage contributing: ` | `S: ` |
| header prefix `Implicated: ` | `F: ` |
| separate `Packet` column, showing `<pair_id>.html` | the `Pair ID` cell carries the link |
| fixed auto-context columns | chosen per sample (below) |

`N/A` is plain text, not Excel's `#N/A` error value, so it still compares equal
to the string and still round-trips through `data_only=True`. The single-letter
prefixes are explained on the `instructions` and `guidance` sheets, so a
reviewer meeting `S: evidence_extraction` for the first time can look it up.

## The auto-context block is now sized to the sample

Two policies decide whether a context column is written, both declared per
column in `_CONTEXT_COLS` ([scle/workbook.py](../src/llm_dup_dev/study_eval/scle/workbook.py)):

- **constant** — the column holds one value for the whole workbook, so it is
  stated once on the `metadata` sheet and no column is written. This always
  removes `Component` (the metadata sheet already carried
  `evaluation_component`); it removes `Reference set` unless a recall sample
  spans several sets; and it removes `Screening retrieved` from a precision
  workbook, where every pair is screening-positive by construction, adding a
  `screening_retrieved` metadata row instead.
- **never populated** — no sampled pair fills the column, so it is dropped
  outright: `Joint stratum` on a precision sample, `Confusion cell` on a
  screening-observed recall sample, and `Screening retrieved` /
  `Methods disagree` where screening was not evaluated for any sampled set.

Only columns carrying an explicit policy behave this way. A column such as
`Misclassified` is never dropped for merely looking uniform in a small draw.

Consequences for anything reading a workbook:

- **Do not assume a fixed column order or a fixed column set.** Look columns up
  by header name. `read_reviewer_submission` and `read_arbitration` already do,
  and only `Pair ID` is required — a pair sheet without it is now rejected with
  a clear message rather than reported as "every row missing".
- **Read displaced context from `metadata`**, not from the pair sheet. The
  values are rendered exactly as the column would have shown them (`yes` / `no`
  / `N/A`), not as Python booleans.
- The arbitration workbook follows the same rules: `pairs` sheet, linked pair
  id, `Difficulty` in its dimension headers, and no stratum column its
  arbitrated pairs do not populate.

## Formatting

Presentational only, but worth knowing if you diff generated files: every cell
on the pair sheet is top-aligned, labels and enum values are centred, prose
columns (decision trace, free-text assessment, source of error,
method-difference explanation, and the arbitration free-text columns) are
left-aligned and wrapped, each column has an explicit width, and panes freeze
below the header row and right of the pair id.

## Checklist for the secure side

- [ ] Regenerate any reviewer or arbitration workbook issued before this
      version; the old ones will not read back.
- [ ] Replace `wb["cases"]` with `wb["pairs"]` in any local tooling.
- [ ] Replace comparisons against `"not_applicable"` with the `NOT_APPLICABLE`
      constant (now `"N/A"`) imported from `scle.workbook`.
- [ ] Replace `"Case difficulty"` header lookups with `"Difficulty"`, and build
      generated-column headers from `STAGE_HEADER_PREFIX` /
      `DOMAIN_HEADER_PREFIX` rather than the literal old prefixes.
- [ ] Drop any dependence on a `Packet` column; the hyperlink is on `Pair ID`.
- [ ] Keep the `packets/` folder beside the workbooks — the link targets are
      unchanged relative paths.
