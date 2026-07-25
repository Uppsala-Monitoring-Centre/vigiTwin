# `study_eval` — performance-evaluation package

`llm_dup_dev.study_eval` implements the deterministic and administrative parts of
a case-level method-evaluation study — aggregate precision/recall estimates plus
a structured case-level examination (SCLE) with two reviewers and arbitration.
It is generic: nothing is hardcoded to a particular screening method or set of
reference sets. It is developed and tested entirely with synthetic data in this
repository; the secure repository (`llm_dup_pv_secure`) adds a thin adapter that
builds the input models from a frozen run and invokes the workflow.

The package is a subpackage of `llm_dup_dev` (it reuses `eval`, `review_html`,
`models`, and `run_manifest`), but nothing else in `llm_dup_dev` imports it.

## The four steps

The study runs interactively — typically in a notebook on the secure side — with
human input between steps. Each step is a function in
[workflow.py](../src/llm_dup_dev/study_eval/workflow.py) that reads the previous
step's artifacts and writes its own plus a checksummed
`evaluation_run_manifest.json`.

| # | Function | In | Out |
| --- | --- | --- | --- |
| 1 | `run_analysis` | reference sets + predictions | validation, confusion matrices + estimates, per-pair prediction/truth table, SCLE sample (+ ordered backups) |
| 2 | `generate_reviewer_workbooks` | an SCLE sample | one reviewer XLSX per reviewer |
| 3 | `build_arbitration` | the two completed reviewer XLSX | inter-reviewer **agreement statistics** + an arbitration XLSX focused on the **disagreement pairs** |
| 4 | `finalize` | reviewer XLSX + completed arbitration XLSX | consensus (preserving originals) + final SCLE tables/summaries |

```python
from llm_dup_dev.study_eval import workflow

# Step 1 — one call handles a mix of precision- and recall-role reference sets.
analysis = workflow.run_analysis(
    reference_sets, predictions, seed="study-2026",
    scle_precision_per_cell=10, scle_recall_per_stratum=10,
    output_dir="results/analysis",
)
sample = analysis.scle_samples["precision::precision_primary"]

# Step 2 — issue the two reviewer workbooks.
books = workflow.generate_reviewer_workbooks(sample, ["reviewer_1", "reviewer_2"], "review")

# ... reviewers fill the workbooks ...

# Step 3 — agreement stats + disagreement-focused arbitration workbook.
build = workflow.build_arbitration(sample, books, "arbitration", arbiter_id="annotator")

# ... arbiter fills the arbitration workbook ...

# Step 4 — consensus + final tables.
final = workflow.finalize(sample, books, build.arbitration_workbook, "final")
```

The **SCLE sample sizes are configurable** (`scle_precision_per_cell` per
confusion cell, `scle_recall_per_stratum` per joint stratum; both default to 10).
Each SCLE draw also produces an **ordered backup list** per stratum;
`study_eval.promote_backup(sample, disqualified_pair_id)` swaps the next-ranked
backup in when a primary pair must be dropped.

### CLI

A thin CLI wraps the same four functions (workbooks require the `study` extra):

```
python -m llm_dup_dev.study_eval analyze   --reference-set precision.json --reference-set fda_gold.json \
                                           --predictions frozen_primary.json --seed study-2026 --output-dir results/
python -m llm_dup_dev.study_eval workbooks  --sample results/scle_sample_precision_*.json --reviewer r1 --reviewer r2 --output-dir review/
python -m llm_dup_dev.study_eval arbitrate  --sample ... --workbook r1.xlsx --workbook r2.xlsx --output-dir arb/
python -m llm_dup_dev.study_eval finalize   --sample ... --workbook r1.xlsx --workbook r2.xlsx --arbitration arb.xlsx --output-dir final/
```

## Input models (generic)

Two model families, both in
[schemas.py](../src/llm_dup_dev/study_eval/schemas.py):

- **`ReferenceSet`** — `set_id`, `role` (`"precision"` or `"recall"`), optional
  `label`/`provenance`, and `pairs: list[ReferencePair]`. A precision set is the
  screening method's candidate set with a per-pair `reference_label`
  (`duplicate` / `non_duplicate` / `abstain`); a recall set is held-out positive
  controls (all known duplicates). The dev side receives **already-constructed**
  sets — the secure side does all set-construction sampling (one pair per
  cluster, held-out eligibility, provenance selection) and sends one homogeneous
  set per provenance. The dev side never samples *within* a reference set; its
  only sampling is SCLE case selection.
- **`PredictionSet`** — `configuration_id` and `predictions: {pair_id:
  PairPrediction}`, where each `PairPrediction` carries the evaluated method's
  `adjudicator_label` (the 4-value vocabulary), the `screening_retrieved` flag,
  and an optional full `ClassificationResult` decision trace.

## Metrics

- Precision: screening baseline precision, adjudicator precision, conditional
  recall, false-positive rejection, proportion removed, abstention rate, and the
  full adjudicator×reference cross-classification.
- Recall (per set): `recall_screening`, `recall_serial` (retrieved by screening
  **and** adjudicator-positive), `recall_adjudicator_ceiling`, the joint 2×2
  (`both`/`neither`/`screening_only`/`adjudicator_only`), and the descriptive
  recall reduction.
- All proportions carry a two-sided 95% **Wilson** confidence interval
  ([stats.py](../src/llm_dup_dev/study_eval/stats.py)); `ci_method` is recorded
  on every estimate.

## Secondary analyses

Secondary analyses are **not** part of the SCLE workflow. Run them as **Step 1
only** with `sample_scle=False`, either passing a different `PredictionSet`
(a genuinely different frozen configuration) or a re-interpretation `variant`
(`AnalysisVariant.ABSTAIN_POSITIVE` / `OTHERWISE_RELATED_POSITIVE`). They produce
the aggregate tables and the per-pair prediction table, no SCLE artifacts.

## SCLE reviewer workflow details

- **Reviewer workbook** ([scle/workbook.py](../src/llm_dup_dev/study_eval/scle/workbook.py)):
  greyed auto-context columns on the left (filled by the package — reference
  label, adjudicator output, confusion cell / joint stratum, decision trace),
  dropdown-validated response columns on the right. Non-applicable dimensions are
  pre-filled `not_applicable`. Excel protection is advisory, so **read-back is
  the source of truth**: `read_reviewer_submission` re-validates vocabularies,
  applicability, coverage, and an embedded sample checksum.
- **Agreement** ([scle/agreement.py](../src/llm_dup_dev/study_eval/scle/agreement.py)):
  per structured dimension, percent agreement and Cohen's kappa, plus an overall
  percent agreement.
- **Arbitration** focuses on the structurally-disagreeing pairs only; agreement
  pairs are auto-resolved from the reviews at finalize time.
- **Consensus** ([scle/consensus.py](../src/llm_dup_dev/study_eval/scle/consensus.py))
  preserves both reviews verbatim (with checksums); every consensus dimension
  records whether it came from `reviewer_agreement` or `arbitration`. Primary
  reference labels are never altered; reference-label concerns are collected for
  an explicitly-labelled sensitivity analysis only.

## Secure-side integration

The secure adapter:

1. **Builds the input models** from the frozen run: a `ReferenceSet` per
   reference set (already constructed), and one `PredictionSet` per frozen
   configuration (`adjudicator_label` + `screening_retrieved`, optionally the
   `ClassificationResult` trace). For recall, the adjudicator must be run under
   complete candidate coverage (a prediction for every sampled pair). Pair ids
   are orientation-sensitive (`{report_id1}__{report_id2}`) and must be
   consistent across the reference sets and the predictions.
2. **Implements `PacketContentProvider`**
   ([scle/packets.py](../src/llm_dup_dev/study_eval/scle/packets.py)) over
   confidential storage if it wants static per-pair review packets (built on
   `review_html`). Confidential content never enters the generic schemas.
3. **Runs the four steps in order**, storing all workbooks and outputs inside the
   secure environment, and re-runs Step 1 (with `sample_scle=False`) for each
   secondary configuration.

Before workbooks are generated, freeze the `FEATURE_DOMAINS` list in
[scle/form.py](../src/llm_dup_dev/study_eval/scle/form.py) and note the
confidence-interval method (this package prespecifies Wilson).

## Regenerating schemas and examples

The committed `schemas/study_*.schema.json` and `examples/study_*_example.json`
are drift-checked by `tests/test_study_eval/test_schemas.py`. After changing a
model:

```
python scripts/generate_study_eval_schemas.py
```
