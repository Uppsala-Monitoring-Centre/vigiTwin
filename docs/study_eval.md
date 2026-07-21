# `study_eval` — performance-evaluation package

`llm_dup_dev.study_eval` implements the deterministic and administrative parts
of the duplicate-adjudication performance-evaluation study described in
[performance_evaluation_description.md](performance_evaluation_description.md). It
is developed and tested entirely with synthetic data in this repository; the
secure repository (`llm_dup_pv_secure`) adds a thin adapter that maps real
reference sets and frozen method outputs into the package's generic schemas and
invokes it.

The package is a subpackage of `llm_dup_dev` (it reuses `eval`, `review_html`,
`models`, and `run_manifest`), but nothing else in `llm_dup_dev` imports it, so
the boundary stays clean.

## Workflow (stages)

The study runs as a sequence of file-mediated, idempotent stages. Each stage
reads the validated artifacts of the previous one and writes its own plus a
provenance manifest (`evaluation_run_manifest.json`) whose `inputs`/`outputs`
reference files by sha256 — a verifiable chain from canonical inputs to the
final tables.

```
inputs (secure adapter)                 study_eval stage             output
─────────────────────────────────────   ──────────────────────────   ───────────────────────
recall reference set (candidates)     → sample-recall              → sampled recall set + record
precision set + method outputs        → aggregate                  → precision/recall tables (all variants)
recall sets + method outputs          ↗
analysis input                        → scle-sample                → SCLE case list (stratified)
SCLE sample + packet content*         → packets (Python API)       → static HTML reviewer packets
SCLE sample                           → workbooks                  → 2 reviewer workbooks (XLSX)
2 filled reviewer workbooks           → compare                    → discrepancy report
discrepancy + 2 submissions           → arbitrate                  → arbitration workbook (XLSX)
2 submissions + arbitration workbook  → consense                   → consensus + SCLE result tables
```

`*` Packet generation needs confidential report content and is therefore a
Python API (`study_eval.scle.packets.generate_packets`), driven by the secure
adapter, not a file-only CLI stage.

### CLI

```
python -m llm_dup_dev.study_eval <subcommand> [options]
```

Subcommands: `sample-recall`, `aggregate`, `scle-sample`, `workbooks`,
`compare`, `arbitrate`, `consense`. Reviewer/arbitration workbooks require the
optional `study` extra (openpyxl): `pip install 'llm-dup-dev[study]'`.

Example — aggregate precision and recall (all variants) from frozen inputs:

```
python -m llm_dup_dev.study_eval aggregate \
    --precision-set precision_set.json \
    --recall-set fda_gold.json --recall-set aemps.json \
    --method-outputs frozen_primary.json \
    --output-dir results/primary/
```

## Modules

| Module | Responsibility |
| --- | --- |
| `schemas` | Canonical pair-level input models + validators |
| `loading` | JSON read/write and analysis-input assembly |
| `stats` | Wilson 95% CIs (`EstimateWithCI`) — stdlib only |
| `aggregate` | Precision/recall metrics + the analysis variants |
| `sampling` | Seeded cluster + stratified SCLE sampling |
| `scle.form` | SCLE dimensions, auto-context, applicability, assessment |
| `scle.packets` | Static reviewer packets (via `review_html`) |
| `scle.workbook` | Reviewer XLSX generation + validated read-back |
| `scle.discrepancy` | Structured comparison of two submissions |
| `scle.arbitration` | Arbitration XLSX generation + read-back |
| `scle.consensus` | Consensus merge preserving originals |
| `results` | Final CSV/JSON tables + markdown summary |
| `manifest` / `checksums` | Provenance manifest + sha256 chain |

## Key design decisions

- **Confidence intervals: Wilson score.** The protocol requires 95% CIs but
  names no method; Wilson is prespecified (closed-form, stdlib, well-behaved at
  n ≈ 50–100 and near 0/1). The method name is recorded on every estimate;
  changing it touches only `stats.py`.
- **Positivity as an explicit rule** (`AnalysisVariant`) over the four-value
  adjudicator output, not `eval.AbstainMode` — the latter cannot express the
  "otherwise-related as positive" secondary analysis and its `DECIDED_ONLY`
  semantics contradict the protocol's primary rule (abstain = negative).
- **Two label vocabularies kept distinct:** the human reference label
  (`duplicate` / `non_duplicate` / `abstain`) and the adjudicator output
  (`Duplicates` / `Otherwise_related` / `Unrelated` / `Abstain`).
- **Read-back is the source of truth** for workbooks. Excel protection is only
  advisory, so submissions are re-validated against the originating sample
  (vocabularies, applicability, coverage, and an embedded sample checksum).
- **Consensus preserves originals.** The two reviews are stored verbatim with
  their checksums; every consensus dimension records whether it came from
  reviewer agreement or arbitration. Primary reference labels are never altered;
  reference-label concerns are collected for transparent, explicitly-labelled
  sensitivity analysis only.

## Secure-side integration

The secure adapter's responsibilities:

1. **Build the input models** from real data and validate them:
   - `PrecisionEvaluationSet` (50 vigiMatch candidates + human labels);
   - one `RecallEvaluationSet` per reference set (positive-control duplicates,
     `cluster_id` required, `provenance` = manual / e2b_derived);
   - `MethodOutputs` per frozen run (`adjudicator` keyed by `pair_id` carrying
     `final_label` and optionally the full `ClassificationResult` trace;
     `vigimatch_retrieved` flags). The adjudicator must be run under complete
     candidate coverage for recall (a verdict for every sampled pair).
   Pair ids are orientation-sensitive (`{report_id1}__{report_id2}`); the
   adapter must guarantee consistent orientation across vigiMatch output,
   reference sets, and adjudicator runs. The models reject a flipped-orientation
   duplicate and more than one pair per cluster.
2. **Implement `PacketContentProvider`** (`scle.packets`) over confidential
   storage, returning `PacketContent` (the two `Report`s plus optional
   `PairFeatures` / `ClassificationResult` / `FrozenPairBundle`). Confidential
   content never enters the generic schemas.
3. **Invoke the stages in order**, storing all workbooks, packets, and outputs
   inside the secure environment. Pass `secure_context` into the manifest for
   deployment metadata (redacted automatically).

Before workbooks are generated, two study parameters must be frozen: the
`FEATURE_DOMAINS` list in `scle.form`, and the confidence-interval method (this
package prespecifies Wilson).

## Regenerating schemas and examples

The committed `schemas/study_*.schema.json` and `examples/study_*_example.json`
are drift-checked by `tests/test_study_eval/test_schemas.py`. After changing a
model, regenerate them:

```
python scripts/generate_study_eval_schemas.py
```

## Open study-design questions (see the design report)

Recorded here so they are not lost: the CI for the descriptive recall-reduction
difference; the primary denominator for "proportion removed" (evaluable vs all
candidates); manual-vs-E2B priority granularity for AEMPS/Lareb; the truncated
MHRA paragraph in the protocol; whether one frozen adjudicator output can serve
both recall estimands; and the contents of the `FEATURE_DOMAINS` list.
