# Migration notes: the analysis variant became study configuration

**Audience:** the secure-side deployment (`llm_dup_pv_secure`) and any tooling
that calls `study_eval.workflow.run_analysis`, the aggregate analysis functions,
or the result writers. This document describes the contract changes only; no
secure-side code is modified from this repository.

## Summary

The positive class — which of the adjudicator's four output labels count as a
duplicate-positive classification — used to be a fixed dev-side enum,
`AnalysisVariant`, with exactly three members. That put study configuration in
library code, and the consequence was concrete: the study protocol's own
secondary analysis 2 is "*Abstain* **and** *Otherwise related* classified as
duplicate-positive", while `OTHERWISE_RELATED_POSITIVE` was
`{Duplicates, Otherwise_related}` only. The prespecified analysis could not be run
without changing this package.

The rule is now a value the caller supplies: `PositiveClass(name,
positive_labels)`. This package owns the adjudicator's output vocabulary
(`ADJUDICATOR_LABELS` — `Duplicates`, `Otherwise_related`, `Unrelated`,
`Abstain`), because that is the method's own contract, and it owns the mechanism:
a rule's labels are validated as a non-empty subset of that vocabulary, and a
blank name is rejected. It does **not** own which rules a study runs, what they
are called, or which one is the baseline. That is the same division
[`scle/vocabularies.py`](../src/llm_dup_dev/study_eval/scle/vocabularies.py)
already applies to the SCLE error-stage and feature-domain lists, so the study now
injects frozen configuration in two places rather than one.

Two defects fell out of the same root cause and are fixed here. First, the SCLE
samplers derived each case's `methods_disagree`, `is_misclassified` and
`confusion_cell` under the *primary* rule regardless of the rule the analysis
actually ran under, while taking the strata from the rule-aware result. Under any
non-primary rule the two disagreed inside a single case record: a recall pair
whose adjudicator label was `Abstain` landed in the `both` stratum — positive
under `ABSTAIN_POSITIVE` — while being flagged `is_misclassified=True`. Because
those two fields gate which conditional dimensions a reviewer is asked, a reviewer
would have been asked the wrong questions about that pair. Second, both
`write_secondary_comparison_csv` and `summary_to_markdown` located their baseline
by matching the literal string `"primary"`, and on finding no such result emitted
a header-only CSV and a summary containing nothing but its title — silently, and
reachable by any run under a non-primary rule.

A consequence worth stating plainly: **SCLE sampling and the collapse rule are now
independent.** Examining cases under a secondary rule is a supported
configuration, not a warned-about one (the warning that fired for it is gone, as
its claim about the strata was only half true), and running the primary rule
aggregate-only is equally supported. This study does the latter for its secondary
analyses — that remains a study-execution choice, now expressed in the secure-side
runner rather than nudged by the package.

## API changes

| Before | After |
|---|---|
| `AnalysisVariant.PRIMARY` etc. (enum) | `PositiveClass(name, positive_labels)`; `PRIMARY`, `ABSTAIN_POSITIVE`, `OTHERWISE_RELATED_POSITIVE` exported as convenience constants |
| `run_analysis(..., variant=...)` | `run_analysis(..., positive_class=...)`, default `PRIMARY` |
| `run_precision_analysis(rset, preds, variant)` | `run_precision_analysis(rset, preds, positive_class)` |
| `run_recall_analysis(rsets, preds, variant)` | `run_recall_analysis(rsets, preds, positive_class)` |
| `run_precision_variants(rset, preds)` → dict keyed by enum | `run_precision_variants(rset, preds, variants)` → dict keyed by **name**; `variants` required |
| `run_recall_variants(rsets, preds)` | `run_recall_variants(rsets, preds, variants)`, same |
| `REINTERPRETATION_VARIANTS` | removed — which rules to run is not a package default |
| `sample_scle_precision(...)` / `sample_scle_recall(...)` | both take a required keyword `positive_class` |
| `write_study_artifacts(...)` | takes `baseline=<rule name>`, required when results are supplied |
| `write_secondary_comparison_csv(...)` | takes required keyword `baseline` |
| `summary_to_markdown(...)` | takes required keyword `baseline` |

`PositiveClass` is frozen and hashable. Empty rule lists and repeated rule names
are rejected by the `*_variants` helpers, since results are keyed by name.

## Output changes

- `secondary_comparison.csv` is written **only when the supplied results span two
  or more rules**. A single-rule run — which is what one `run_analysis` call
  produces — no longer writes the file at all, so it disappears from that run's
  output directory and from the `outputs` list in its manifest.
- That file's `primary_proportion` column is renamed **`baseline_proportion`**.
- `summary.md` headings now name the rule: `## Precision (primary)`,
  `## Recall (primary), by reference set` — and correspondingly for any other
  designated baseline.
- `EvaluationRunManifest` gains **`analysis_positive_labels`** (sorted list)
  beside the existing `analysis_variant`. Both are needed now that names are
  study-supplied: `analysis_variant: "abstain_positive"` alone does not tell a
  later reader what was counted as positive. `schemas/` and `examples/` are
  regenerated accordingly.

The `variant` column in every metrics table keeps its name and now holds the
rule's `name`. No other column, estimand, CI, stratum definition, seed or
sampling order changed.

## What the secure side needs to do

1. **Upgrade the installed `llm-dup-dev` to 0.9.0.**
2. **Define the study's rules in one frozen config block**, next to the existing
   `error_stages` / `feature_domains` vocabularies, so all study configuration
   sits together. The protocol's three analyses are:

   ```python
   from llm_dup_dev.study_eval import PositiveClass

   PRIMARY_RULE = PositiveClass(
       name="primary", positive_labels=frozenset({"Duplicates"}))
   SECONDARY_ABSTAIN = PositiveClass(
       name="abstain_positive",
       positive_labels=frozenset({"Duplicates", "Abstain"}))
   SECONDARY_ABSTAIN_AND_OTHERWISE = PositiveClass(
       name="abstain_and_otherwise_positive",
       positive_labels=frozenset({"Duplicates", "Abstain", "Otherwise_related"}))
   ```

   The first two are also importable as `PRIMARY` and `ABSTAIN_POSITIVE`. The
   third has **no** package constant, and is *not* the same rule as the old
   `OTHERWISE_RELATED_POSITIVE` — see the check below.
3. **Replace every `variant=AnalysisVariant.X` with `positive_class=<rule>`.**
   The import of `AnalysisVariant` will fail outright, so no call site can be
   missed silently.
4. **Pass `baseline=<rule name>` anywhere results are pooled** into
   `write_study_artifacts`, `write_secondary_comparison_csv` or
   `summary_to_markdown`. A baseline absent from the supplied results now raises
   `ValueError` instead of producing an empty artifact.
5. **Pass `per_stratum=5` for the recall SCLE draw.** The package default is 10
   and the protocol specifies five pairs per joint stratum (20 per reference set).
   Nothing errors if it is omitted — this is the one study number the package
   still carries a differing default for. The precision default of 10 already
   matches the protocol.

## Checks before the frozen run

- [ ] **Each rule's `positive_labels` matches the protocol text, not just its
      name.** In particular, secondary analysis 2 is *Abstain* **and** *Otherwise
      related* — three labels. The retired `OTHERWISE_RELATED_POSITIVE` constant
      carries only two and would silently answer a different question; it is kept
      for convenience but is not this study's secondary analysis 2.
- [ ] **One rule is designated the baseline** and that name is what every
      `baseline=` argument receives.
- [ ] **Reference-abstain handling is unchanged and must stay that way.** A
      *reference* label of `abstain` is excluded from the precision denominator;
      `Abstain` as an *adjudicator* label is governed by the rule. The rule cannot
      affect the exclusion, and no configuration should be added that tries to.
- [ ] **Decide SCLE per rule deliberately, now that nothing nudges it.** For this
      study: sample under the primary rule, and run both secondary analyses with
      `sample_scle=False`.
- [ ] **Any `secondary_comparison.csv` from an earlier version that pooled more
      than one precision set should be regenerated** (that mis-attribution fix
      shipped in this same release), and consumers reading its
      `primary_proportion` column must be updated to `baseline_proportion`.
- [ ] **Manifests from the frozen run carry both `analysis_variant` and
      `analysis_positive_labels`.** Spot-check one: the labels are what make the
      run interpretable later.
