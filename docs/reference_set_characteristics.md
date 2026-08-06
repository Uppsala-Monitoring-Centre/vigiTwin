# Reference-set characteristics ("Table 1")

`llm_dup_dev.characterization` describes **what is in a reference set** —
reporting years, countries, patient age and sex, the commonest drugs and adverse
events, and how often the two reports of a pair agree on country, year and sex.
It answers the question a reader of a performance evaluation asks first: *which
reports were these estimates computed over?*

It runs at two points, and the artifact is the same both times:

1. at the end of extracting data for an evaluation set, beside the
   `dataset_manifest.yaml` that pins the snapshot the numbers describe;
2. before executing the duplicate adjudication set against the reference set, as
   a check that the sets are what the protocol says they are.

The package is generic. It takes a
[`ReferenceSet`](../src/llm_dup_dev/study_eval/schemas.py) plus access to the
`Report` objects it names, and returns an aggregate-only artifact containing no
report content. All the arithmetic is dev-side and unit-tested against synthetic
reports; the secure side supplies the reports through the `ReportLoader`
protocol it already implements for the pipeline.

**It is descriptive, never inferential.** No proportion here carries a
confidence interval, because these numbers describe the sets as constituted
rather than a sample drawn from a population. Performance estimation is
[`study_eval`](study_eval.md)'s job and stays there.

## Running it

### Freeze time, secure side

```python
from llm_dup_dev.characterization import (
    build_characterization_report, write_characterization_report,
)

report = build_characterization_report(
    [precision_set, *recall_sets],
    reports_by_id,                   # or your SqlReportLoader
    dataset_id=dataset_manifest.dataset_id,
    notes="reports as loaded post narrative-demographics merge",
)
write_characterization_report(validation_set_dir, report, formats=("json", "md"))
```

Passing every set in one call matters: reports shared between sets are fetched
once for the whole run, so the cost to the database is one lookup per *distinct*
report rather than one per set.

### Before the analysis

```
python -m llm_dup_dev.characterization \
    --reference-set precision.json --reference-set fda_gold.json \
    --reports validation_reports.json \
    --output-dir results/characteristics
```

`--reference-set` reads the same `study_reference_set.schema.json` documents
`study_eval analyze` reads, so no separate export is needed. `--reports` is a
JSON array of `Report` objects, matching `scripts/extract_frozen_*.py`. Other
flags: `--top-n` (default 10), `--dataset-id`, `--format` (repeatable; `json`
and `md` by default, `csv` on request), `--allow-missing-reports`, `--notes`.

### Why this is not a `study_eval` subcommand

`study_eval` deliberately never handles `Report` objects — its whole input
contract is pair ids, labels and predictions. Adding a `reports=` parameter to
`run_analysis`, or a subcommand to its CLI, would put report content into the
signature of the performance analysis and make `study_eval` import this package.
Nothing in `llm_dup_dev` imports `characterization`; a test
(`tests/test_characterization/test_no_inbound_imports.py`) keeps it that way.

The artifact carries its own provenance (`created_at`, `characterization_id`,
`dev_package`, `dev_git_commit`, `dataset_id`) inline, like `DatasetManifest`,
rather than joining the `EvaluationRunManifest` stage chain.

## The resolution policy

A `Report` deliberately preserves **every** candidate value per demographic — a
report may carry age 54 from the structured database and 55 from narrative
extraction, and the pipeline compares whole candidate sets (`v1 ∩ v2 ≠ ∅`).
Tabulation needs one row per report, so the reduction happens in
[`policy.py`](../src/llm_dup_dev/characterization/policy.py) and the rule it
applied is recorded in the artifact itself, as `policy`.

1. **Empty list is missing.** Uncertainty sentinels were already dropped by the
   `Report` validators (`models/demographics_vocab.py`); this package inherits
   that contract rather than re-implementing it.
2. **Structured wins.** If any candidate is tagged `structured`, only the
   structured candidates are considered; otherwise all are. Table 1 therefore
   describes the source database, falling back to narrative extraction only
   where the database said nothing.
3. **Canonicalise, then compare.** Each surviving candidate is mapped to its
   *tabulation key* and the distinct keys counted. One key resolves; more than
   one is `conflicting`; none parseable is `unrecognised`.

Step 3 is what makes `conflicting` mean something: the comparison happens at the
granularity the value will be *tabulated* at, not on the raw value. `54.0 years`
and `648 months` both key to completed year `54` and agree; a day-resolution
`2020-06-12` and a year-resolution `2020` both key to `2020`. Only a
disagreement that would have changed a table cell is called a conflict.

| Field | Tabulation key |
| --- | --- |
| `report_date_values` | `int(value[:4])`. Every stored value is ISO-8601, so `resolution` is never a gate — a year-resolution date yields a year exactly as a day-resolution one does. Agrees with `feature_generation.reporting_context._extract_years`. |
| `country_of_origin_values` | Stripped and lowercased; **otherwise verbatim** (see below). Display label is the most frequent original spelling, ties lexicographic. |
| `patient_sex_values` | The value, already constrained to `male`/`female` by the model. |
| `patient_age_values` | **Completed years**: `years → v`, `months → v/12`, `days → v/365.25`, then floor. Any other unit is `unrecognised` — `AgeValue.unit` is a bare `str`, not a `Literal`, so reading an unexpected unit as years could age a patient twelvefold. |
| `Drug.substance` | Stripped and lowercased, deduplicated within a report. |
| `AdverseEvent.pt_name` | Stripped and lowercased, deduplicated within a report. |
| `AdverseEvent.primary_hlt_name` | Same; `None` contributes nothing. |

**Country is left verbatim on purpose.** The data contract declares no code
system for `country_of_origin_values`, so mapping `"US"` onto `"United States"`
here would be this package inventing a standard. The consequence is made visible
rather than hidden: the country distribution is emitted **in full** with
`n_distinct`, so a mixed-vocabulary extract shows up as two categories a reader
can recognise as one country. Check that table before quoting the pair-level
same-country figure — a loader that spells the same country two ways will score
such pairs as *different*.

**Which `Report` objects you pass changes the numbers.** Reports read straight
from the database and reports that have been through
`merge_narrative_demographics` resolve differently, and nothing in the artifact
can detect which it was. Record it in `notes=`.

## What is computed

Report-level statistics are over **unique reports** — a report appearing in two
pairs is counted once — with `n_report_instances` stated beside
`n_unique_reports` so the inflation a per-pair tabulation would have introduced
is visible. `n_unique_reports` is the denominator of every report-level table.

- **Reporting year** and **country** — complete distributions, not top-N.
- **Patient age** — median, IQR and range in completed years, plus 10-year bands
  (`0-9` … `90-99`, `100+`). Quartiles use the inclusive (type-7) definition via
  `statistics.quantiles`; at `n = 1` the median and both quartiles are the single
  value, since a legitimately tiny reference set should not raise. Ages outside
  `[0, 130]` are counted as `n_out_of_range` and excluded from the median, the
  IQR **and** the bands.
- **Patient sex** — `male` and `female` always emitted, count 0 included, so an
  all-female set is legible as one rather than as a one-row table.
- **Top 10 drugs** — counted as *reports naming the substance*, restricted to
  `role ∈ {suspected, interacting}`. A separate `drug_roles` distribution makes
  the exclusion visible.
- **Top 10 adverse events at PT level and at HLT level** — same counting, over
  `pt_name` and `primary_hlt_name`.
- **Field availability** — how many reports carried a narrative, any drug, any
  drug role, any adverse event, any primary HLT, any case identifier.

Ranked lists sort by descending count then key ascending, take the first
`top_n`, and set `boundary_tie` when the cut fell *inside* a run of equal
counts — so a reader knows the excluded values were no rarer than the last one
shown.

Pair-level statistics are a separate section, over the pairs where both reports
were available:

- **Concordance** on country, reporting year and sex. Deliberately three-way:
  `same`, `different`, and `not_comparable` for pairs where either side had no
  usable value. Two reports that are both silent about country have not
  disagreed about it, and folding them into `different` would make the headline
  figure a function of data coverage. Both `proportion_same_comparable` (the
  figure usually wanted) and `proportion_same_all` (which keeps the
  not-comparable mass visible) are reported.
- **Differences** — `|Δ|` in reporting year and in patient age, plus the count of
  pairs with identical age.
- **Shared features** — shared drugs and shared PTs per pair, and how many pairs
  share at least one of each. These reuse
  `feature_generation.matching.compute_matching_drugs` / `compute_matching_aes`,
  so "shared" means exactly what it means to the adjudicator. Note that pair
  overlap ignores `role`, unlike the top-drugs table: overlap is about the two
  reports being alike, which is what the method reasoned over.

### Missingness is a category, never a dropped row

Every categorical distribution satisfies

```
n_resolved + n_missing + n_conflicting + n_unrecognised + n_out_of_range
    == denominator
```

and every numeric summary states what it excluded. A reader can therefore never
mistake "we had no data" for "the value was zero" — the same distinction the
`StatsProvider` contract insists on for frequency counts.

`unavailable_reason` distinguishes *this question could not be asked of this
data* from *the answer is empty*. An empty top-drugs table carries
`missing_drug_role` when no drug in the set recorded a role at all, and an empty
HLT table carries `missing_hierarchy` when no adverse event carried a primary
MedDRA hierarchy (deliberately the same string the deterministic AE
clinical-picture assessor uses). An empty table with **no** reason means the
field was populated and simply held nothing countable.

## Disclosure control: none, by design

Counts are exact and unsuppressed. A country cell of `n = 1` in a small recall
set is in principle a re-identification handle, and this package neither hides
it nor warns about it.

The reasoning is that suppression is a decision about a specific artifact
leaving a specific environment, and it is better made by a person looking at the
numbers than pre-empted by a threshold chosen here. The artifact is a secure-side
output; whether it may be carried out is a human decision each time.
`tests/test_characterization/test_writer.py::TestNoSmallCellSuppression` pins
the absence of suppression, so a future well-meaning change cannot silently
alter published numbers.

## Artifact

`write_characterization_report` writes, into a directory of the caller's
choosing:

| File | Format |
| --- | --- |
| `reference_set_characteristics.json` | The full `CharacterizationReport`. Schema: [`schemas/characterization_report.schema.json`](../schemas/characterization_report.schema.json), example: [`examples/characterization_report_example.json`](../examples/characterization_report_example.json). |
| `reference_set_characteristics.md` | Human-readable summary. Pure formatting — every number was computed in the builder. |
| `reference_set_characteristics.csv` | Opt-in. One long-format table (`set_id, section, field, key, label, statistic, value, denominator`), for re-plotting elsewhere. |

One block per reference set, in the order supplied. There is deliberately **no
pooled across-sets block**: the sets differ in provenance and role, and pooling
would invite exactly the comparison per-set reporting exists to prevent — the
same reason recall is reported per set and never pooled in `study_eval`.
Distributions are **not** split by `reference_label` either; the set's
composition in the human-reference vocabulary is given once, as
`reference_label_counts`.

## Regenerating the schema and example

The committed schema and example are drift-checked by
`tests/test_schemas_examples.py`. The example is a *computed* artifact, so it
has a generator:

```
python scripts/generate_characterization_schema.py
```
