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
  `adjudicator_label` (the 4-value vocabulary), the optional
  `screening_retrieved` flag, and an optional full `ClassificationResult`
  decision trace.

## Screening modes

Each reference set declares, in `screening_mode`, whether screening performance
is part of **its** estimand. The mode is per set, never global, and is never
inferred from a missing file — it must be selected explicitly.

`observed` (the default) is the existing behaviour: screening results are
supplied, every pair needs a `screening_retrieved` verdict, and the screening
and end-to-end estimands are computed. A missing or unusable screening result is
a hard preflight error.

`not_evaluated` is for a reference set supplied directly to the adjudicator with
**no screening-algorithm results at all** — a recall set whose pairs were never
run through screening, for instance. Then:

- no screening verdict is required for any pair;
- all supplied reference pairs are evaluated against the adjudicator
  predictions;
- `recall_screening`, `recall_serial`, `recall_reduction` and the joint strata
  are **not estimated** — they are `null` in `study_results.json`, omitted from
  the recall CSVs, and stated as not evaluated in `summary.md`;
- `recall_adjudicator_ceiling` is still computed, and is a *direct* estimate of
  adjudicator recall over the supplied pairs rather than a ceiling, so the
  human-readable summary labels it that way;
- SCLE cases are drawn from the adjudicator-only strata
  (`adjudicator_positive` / `adjudicator_negative`), since the joint strata need
  a screening verdict.

This is **not** an assumption of perfect screening, and no synthetic
all-positive verdicts are created anywhere. An absent screening figure must not
be read as a one or a zero.

Two rules keep the intent unambiguous:

- A **precision** set may not use `not_evaluated` — it *is* the screening
  method's candidate set, so the combination is rejected by the model.
- A `screening_retrieved` value supplied anyway for a pair in a `not_evaluated`
  set is **accepted and never read**, not rejected. The mode describes the
  estimand, not whether screening data physically exist, so a pair may belong to
  an `observed` set where its verdict is analysed *and* to a separate set where
  screening is deliberately out of scope. Preflight warns, naming the set and
  the number of unused values, so this is never silent.

`EvaluationRunManifest.screening_modes` records every set's mode alongside the
analysis variant and CI method.

## Metrics

- Precision: screening baseline precision, adjudicator precision, conditional
  recall, false-positive rejection, proportion removed, abstention rate, and the
  full adjudicator×reference cross-classification.
- Recall (per set): `recall_screening`, `recall_serial` (retrieved by screening
  **and** adjudicator-positive), `recall_adjudicator_ceiling`, the joint 2×2
  (`both`/`neither`/`screening_only`/`adjudicator_only`), and the descriptive
  recall reduction. All but `recall_adjudicator_ceiling` need screening results,
  so a set with `screening_mode: not_evaluated` reports only that one — see
  [Screening modes](#screening-modes). Recall is reported per set, never pooled,
  so a run mixing the two modes needs no combined screening denominator;
  `RecallResult.screening_evaluated_set_ids` states the scope of the screening
  figures for any consumer that builds one.
- All proportions carry a two-sided 95% **Wilson** confidence interval
  ([stats.py](../src/llm_dup_dev/study_eval/stats.py)); `ci_method` is recorded
  on every estimate.

## Analysis variants and secondary analyses

`run_analysis` takes a `variant` that sets the positive class over the four-value
adjudicator output: `PRIMARY` (positive = `Duplicates`), `ABSTAIN_POSITIVE`, or
`OTHERWISE_RELATED_POSITIVE`. The variant drives both the aggregate estimates and
— when `sample_scle=True` — the SCLE strata (confusion cells / joint strata).

For **this** study, SCLE is examined on the primary configuration and the
secondary analyses are aggregate-only, so run secondaries as **Step 1 with
`sample_scle=False`** (passing a re-interpretation `variant`, or a different
`PredictionSet` for a genuinely different frozen configuration). That produces
the aggregate tables and the per-pair prediction table, no SCLE artifacts.

Sampling SCLE under a non-primary variant is **allowed but warns** — the strata
then reflect that variant's positivity rule. "SCLE only on primary" is a
study-execution policy, not a package rule, so the package permits either and
leaves the choice to the caller; a future study could deliberately sample SCLE
under a different rule.

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

The secure-side adapter is deliberately thin: its whole job is to **map frozen
data into the two input models, then call the four workflow functions**. It does
not reimplement metrics, CIs, sampling, or workbook logic — the package owns all
of that. The package is installed into the secure repo as `llm-dup-dev[study]`
(the `study` extra pulls `openpyxl`); pin the exact version you validate against.

The committed `schemas/study_reference_set.schema.json` and
`schemas/study_prediction_set.schema.json` (with their `examples/`) are the exact
input contracts; use them as fixtures in the secure repo's tests.

### The adapter's two responsibilities

1. **Build a `ReferenceSet` per reference set.** These arrive **already
   constructed** — the secure side does all set-construction sampling
   (one-per-cluster, held-out eligibility, manual/E2B prioritisation) and sends
   one *homogeneous* set per provenance. `role="precision"` carries a per-pair
   `reference_label`; `role="recall"` is positive controls (label absent or
   `"duplicate"`). Set `screening_mode="not_evaluated"` on a recall set whose
   pairs were never run through screening, so no screening estimand is computed
   for it (see [Screening modes](#screening-modes)); leave it at the `"observed"`
   default wherever screening candidate membership is known.
2. **Build a `PredictionSet` per frozen configuration** from the frozen run.
   A frozen `PipelineRunner` run persists its `PairResult`s via
   `pipeline.export.write_pair_results(...)`; read them back with
   `pipeline.export.read_pair_results(path) -> {pair_id: PairResult}` and map:
   `PairResult.final_label` → `adjudicator_label`, `PairResult.classification`
   → the optional `ClassificationResult` trace (include it — it populates the
   reviewer workbook's Decision-trace column and is consistency-checked), and the
   pair's screening (vigiMatch) candidate membership → `screening_retrieved`
   (secure-side knowledge, not part of `PairResult`). Leave `screening_retrieved`
   unset for pairs that only belong to `not_evaluated` sets — never fill it in
   with `True` to satisfy the schema, as that would report screening recall as
   perfect. For **recall**, the adjudicator must be run under **complete
   candidate coverage**: a prediction for *every* sampled pair, regardless of
   screening.

> **Data boundary.** In this repo the frozen run and its `pair_results.json` are
> **synthetic** (tests, examples, the `outputs/` demo). On the secure side they
> are **real** (real-derived `PairFeatures` / `ClassificationResult`, and packets
> load real `Report`s) — that is what the study evaluates. Real `pair_results`
> and packets stay inside the secure environment and never reach the dev repo;
> `write_pair_results` / `read_pair_results` are just serialization and don't
> change that boundary.

### Worked example (secure side)

```python
# secure repo — illustrative; the loaders/PipelineRunner are yours.
from llm_dup_dev.study_eval import (
    ReferencePair, ReferenceSet, PairPrediction, PredictionSet, workflow,
)
from llm_dup_dev.study_eval.scle.packets import PacketContent
from llm_dup_dev.models.pair_identity import canonical_pair_id, canonical_report_ids

def to_reference_pair(r1, r2, cluster, label) -> ReferencePair:
    # Canonical identity: ids ascending, pair_id derived from that order. Never
    # hand-build the id — the ordering rule is numeric for numeric ids, so
    # f"{r1}__{r2}" would put "10" before "9". A noncanonical pair is rejected,
    # not reordered.
    lower, higher = canonical_report_ids(str(r1), str(r2))
    return ReferencePair(
        pair_id=canonical_pair_id(lower, higher),
        report_id1=lower,
        report_id2=higher,
        cluster_id=cluster,               # optional metadata
        reference_label=label,            # required for precision; omit for recall
    )

def to_reference_set(frozen_set) -> ReferenceSet:
    return ReferenceSet(
        set_id=frozen_set.id,
        role=frozen_set.role,                     # "precision" | "recall"
        provenance=frozen_set.provenance,         # one homogeneous set per provenance
        pairs=[
            to_reference_pair(r1, r2, cluster, label)
            for (r1, r2, cluster, label) in frozen_set.rows
        ],
    )

# The frozen run persists its PairResults with
# pipeline.export.write_pair_results(...); read them back keyed by pair_id.
from llm_dup_dev.pipeline.export import read_pair_results

def to_prediction_set(pair_results_path, config_id, screened_pair_ids) -> PredictionSet:
    results = read_pair_results(pair_results_path)   # {pair_id: PairResult}
    return PredictionSet(
        configuration_id=config_id,               # "primary", "llm_ae_clinical_picture", ...
        predictions={
            pid: PairPrediction(
                pair_id=pid,
                adjudicator_label=res.final_label,     # PairResult property
                screening_retrieved=pid in screened_pair_ids,  # secure-side knowledge
                classification=res.classification,     # the frozen decision trace
            )
            for pid, res in results.items()
        },
    )

class SecurePacketProvider:                       # confidential content stays secure-side
    def __init__(self, pair_results):             # {pair_id: PairResult}
        self._results = pair_results
    def get(self, pair_id: str) -> PacketContent:
        r1, r2 = pair_id.split("__")
        res = self._results[pair_id]
        return PacketContent(report_1=load_report(r1), report_2=load_report(r2),
                             features=res.features, classification=res.classification)

# --- run the four steps (primary), all outputs inside the secure environment ---
prec = to_reference_set(frozen_precision_set)
recalls = [to_reference_set(s) for s in frozen_recall_sets]
predictions = to_prediction_set(
    PRIMARY_RUN / "pair_results.json", "primary", screened_pair_ids)

analysis = workflow.run_analysis(
    [prec, *recalls], predictions,
    seed=STUDY_SEED, scle_precision_per_cell=10, scle_recall_per_stratum=10,
    output_dir=SECURE_OUT / "analysis",
)
sample = analysis.scle_samples[f"precision::{prec.set_id}"]

# render packets into a packets/ folder beside the workbooks so the links resolve
from llm_dup_dev.study_eval.scle.packets import generate_packets
primary_results = read_pair_results(PRIMARY_RUN / "pair_results.json")
generate_packets(sample, SecurePacketProvider(primary_results),
                 SECURE_OUT / "review" / "packets")

books = workflow.generate_reviewer_workbooks(sample, ["reviewer_1", "reviewer_2"],
                                             SECURE_OUT / "review")   # packet_dir="packets"
# ... two reviewers fill their workbooks ...
build = workflow.build_arbitration(sample, books, SECURE_OUT / "arb", arbiter_id="annotator")
# ... arbiter fills the arbitration workbook ...
final = workflow.finalize(sample, books, build.arbitration_workbook, SECURE_OUT / "final")

# secondary analyses = Step 1 only, no SCLE:
workflow.run_analysis(
    [prec], to_prediction_set(LLM_AE_RUN / "pair_results.json",
                              "llm_ae_clinical_picture", screened_pair_ids),
    seed=STUDY_SEED, sample_scle=False, output_dir=SECURE_OUT / "sec_llm")
from llm_dup_dev.study_eval import AnalysisVariant
workflow.run_analysis([prec], predictions, seed=STUDY_SEED, sample_scle=False,
                      variant=AnalysisVariant.ABSTAIN_POSITIVE, output_dir=SECURE_OUT / "sec_abstain")
```

### Confidential content
Implement `PacketContentProvider`
([scle/packets.py](../src/llm_dup_dev/study_eval/scle/packets.py)) over
confidential storage for the static review packets; confidential content never
enters the generic schemas. Put the packets in a `packets/` subfolder **beside
the workbooks** so the per-row Packet hyperlinks resolve.

The packets can be produced two ways, and the workbook links resolve for either
because both use the same filename scheme (`review_html`'s `_safe_filename`):
`study_eval.scle.packets.generate_packets` (shown above — it also surfaces the
reference label on the page), or, if you already have a saved run on disk,
`review_html.load_saved_run` / `render_saved_run`
([review_html/saved_run.py](../src/llm_dup_dev/review_html/saved_run.py)), which
reconstructs the run from its `pair_results.json` and renders every pair. Render
the SCLE-sampled pairs into `packets/` either way.

### Pre-flight checklist
- [ ] `ReferenceSet` / `PredictionSet` round-trip against the committed schemas.
- [ ] `pair_id` built with `canonical_pair_id` everywhere. Reference sets and
  predictions then agree by construction; a noncanonical `ReferencePair` is
  rejected rather than reordered, so this fails loudly rather than scoring
  nothing.
- [ ] `screening_mode` set deliberately on every reference set — `not_evaluated`
  only where screening results genuinely do not exist, never to work around a
  missing file.
- [ ] Recall predictions cover **every** sampled pair. For a set with
  `screening_mode: observed`, every pair carries a `screening_retrieved` verdict
  and every precision pair is `screening_retrieved`. `study_eval.preflight`
  checks all of this and reports every problem at once.
- [ ] `FEATURE_DOMAINS` ([scle/form.py](../src/llm_dup_dev/study_eval/scle/form.py)),
  the CI method (Wilson), and the sampling `seed` are frozen and recorded.
- [ ] Read submissions/arbitration back through `read_reviewer_submission` /
  `read_arbitration` (Excel protection is advisory; these are the source of truth).
- [ ] Secondary configurations run as Step-1-only (`sample_scle=False`).
- [ ] All workbooks, packets, and outputs stored inside the secure environment.

## Regenerating schemas and examples

The committed `schemas/study_*.schema.json` and `examples/study_*_example.json`
are drift-checked by `tests/test_study_eval/test_schemas.py`. After changing a
model:

```
python scripts/generate_study_eval_schemas.py
```
