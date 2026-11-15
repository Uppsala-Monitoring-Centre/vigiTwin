# The evidence review format

There are two HTML review formats, and they answer different questions.

The **detailed** format (`docs/integration_guide.md` § Generating HTML review
pages) exists so that someone working on the method can see everything: raw
JSON panels, internal feature identifiers, screening features verbatim, and
side-by-side values coloured by whether they differ. It is built for
annotation, debugging and precision testing.

The **evidence** format exists so that someone who did not build the method
can see *why vigiTwin decided what it decided*. It shows the same two reports,
but every colour on the page is a statement about the stored assessment, and
anything the assessment did not speak to is left neutral.

Both are generated from the same saved-run artifacts. Neither reaches a
database. They write into separate output directories, and the detailed
format's behaviour and output are unchanged by the existence of the second
one.

---

## The colour rule

**Colour follows the assessment, not the diff.** The evidence builder performs
no comparison of its own: it never asks whether report A's value equals report
B's. Every colour is a join from a stored record back onto the report element
that record names.

| Colour | Meaning | Source records |
|---|---|---|
| **Green** | The assessment counted this as matching evidence, or named it as part of the anchor pattern | `matching_drugs`, `matching_AEs`, `anchor`, `feature_contributions` |
| **Red** | A **retained** inconsistency named this element | `inconsistencies` (not suppressed), `clinical_date_findings` |
| **Neutral** | Everything else | — |

"Everything else" is the category that does the work, and it is deliberately
large. It holds agreements that were never scored, differences vigiTwin did
not record as inconsistencies, values absent from one or both reports, values
withheld by a later confidentiality transformation, and findings that were
suppressed.

Consequences worth stating, because each is a case where a diff-coloured page
would say something vigiTwin did not:

- **A drug or adverse event present in only one report is neutral** unless a
  finding names it. One-sidedness is not by itself evidence against the pair:
  reports differ in completeness all the time.
- **Two identical report dates are neutral.** `report_date` is excluded from
  the probability computation, so agreement there earns no evidential credit.
- **A shared country is neutral.** Country defines the reporting context — it
  selects the comparison population the rarity calculation runs against — and
  contributes no term of its own. (Structurally: `patient_country` has no
  entry in the domain-size map, so it can never yield a contribution.)
- **A retained inconsistency stays red on an `Unrelated` pair**, where no
  same-case decision was reached. The finding was still recorded; what did not
  happen is the weighing of it.
- **Green on a drug or AE term is coarser than it looks.** The drug set
  contributes one figure, not one per substance, so green on a term means
  "member of a shared pattern that contributed", not "this term contributed
  +X decades". The page's legend says so.

### Suppression and downgrading are different mechanisms

They are easy to conflate and must not be, because they colour differently.

| | Suppression | Downgrading |
|---|---|---|
| Record type | `Inconsistency` only | `ClinicalDateFindingSummary` only |
| Marker | `suppressed` (+ `suppressed_by`) | non-empty `downgrades` |
| Set by | the LLM AE clinical-picture assessor | the clinical-date module |
| Effect on the penalty | **zero** | **counted, at the reduced severity** |
| Colour | **neutral**, listed under *Superseded findings* | **red**, at its *final* severity |

A suppressed finding is neutral but not invisible: it appears in its own
subsection, so a reader can see that something was considered and set aside
rather than never noticed.

---

## Page structure

One report pair is one `<section class="case">`. A document holds one or more
of them, which is how a single page and a multi-example collection are the
same template.

A case opens with its title, optional human-authored lede, and a row of
outcome tiles — the reference label (with the set it came from), the screening
outcome, the vigiTwin classification, and the SCLE state. These are four
separate verdicts and the page keeps them separate; none is ever inferred from
another.

Then four numbered sections:

1. **Report comparison** — patient characteristics, drugs, adverse events,
   narratives. The report values, coloured by the rule above.
2. **Context and matching evidence** — the reporting context, the anchor
   population, the shared drug and AE terms spelled out, the per-feature
   contributions in decades, and the reconciliation row (below).
3. **Inconsistencies** — retained findings with their domain, severity and the
   element each names, then *Superseded findings* for the suppressed ones.
4. **vigiTwin assessment** — the two-stage decision, and the prose account.

### Row ordering

Patient characteristics keep a **fixed field order** (sex, age, country,
report date, height, weight) and are never reordered by evidence: a reader
comparing two cases should find the same field in the same place.

Drugs and adverse events are ordered by evidence category, not alphabetically
and not by whichever report happened to list them first.

**Drugs** sort by presence, then by role, then by name:

```
both reports          → key drugs and others together, by name
report A only         → key drugs (suspected, interacting) first, then the rest
report B only         → key drugs first, then the rest
```

**Adverse events** sort by presence, then by evidence, then by term — and the
two one-sided groups run in *opposite* directions:

```
both reports          → by term
report A only         → retained inconsistencies first, then neutral terms
report B only         → neutral terms first, then retained inconsistencies
```

The mirroring puts the terms vigiTwin did *not* treat as inconsistent into a
single band in the middle of the table rather than in two separate places.
Those are exactly the terms a reader wants to read against each other — a term
coded differently on the two reports, say — so they belong adjacent.

A cell carrying several attributions takes the strongest role:
`INCONSISTENT` > `ANCHOR` = `MATCHING` > `NEUTRAL`. Red wins because it is a
claim the assessment made *against* a value, where green is additive. Both
attributions stay on the cell, so a tooltip can show both. A row's own tint is
the strongest role among its cells, so a row holding a matched date and a
flagged substance reads as flagged while keeping the green on the date.

Note what row role does *not* do: it does not move the row. Position is set by
presence, because a reader scanning the drug table is first asking which
report listed what. Evidence orders rows only inside a presence group.

---

## The assessment summary

`build_assessment_summary(features, classification, *, run_settings=None)`
returns a short readable account of one pair's assessment. It is pure: no LLM,
no I/O, no templates, and the same inputs always produce the same text. Its
sentences fill fixed slots — shared pattern, anchor, additional matching
evidence, direct evidence, discrepancies, independence decision, same-case
decision — so a table and a paragraph render the same objects and cannot
drift apart.

Sentence *order* depends on the outcome, because the two outcomes read
naturally in different orders. Where independence was rejected, the findings
come before the decision that weighed them. Where it was not, the decision
comes first and the findings follow with the explicit note that they were
never weighed — which is the fact a reader most needs and the thing an
undifferentiated template would bury.

What the generator will not do:

- It never quotes a numeric threshold unless a run manifest supplied it.
- It names the decision pathway actually recorded, rather than assuming the
  pair was decided on rarity. A pair whose independence was rejected on a
  shared source says so, and does not cite its matching score as the reason.
- It says explicitly when no inconsistency-penalty comparison was made.
- It never presents a chance-match probability as a probability of
  duplication, and never prints `p_match`, `p_joint`, "probability",
  "L_match", "pipeline" or "algorithm". The reader-facing vocabulary is
  "matching score", "inconsistency penalty", "anchor population", "reporting
  context", and "vigiTwin".

### Human interpretations

A curated example may carry a `case_interpretation`, written by a person, and
`summary_mode` decides what the page shows: the generated account
(`deterministic`), the human's reading in its place (`interpretation`, the
default once one is supplied), or both with the human's first (`both`).

Human text lives only in the manifest, and the generator has **no write path
to it** — not as a policy but by construction. `--export-drafts` writes the
generated summaries to a *separate* file as a starting point for editing, and
refuses to overwrite an existing one without `--force`, because by the second
run that file usually holds edited text.

---

## Run settings, and why the contributions may exceed the score

The per-feature contributions in section 2 do not always sum to the stored
matching score, and a supplement whose own table visibly fails to add up is
worse than one that explains itself. Two things separate the two numbers:

1. **The anchor adjustment.** An anchor population of *N* reports carries a
   multiplicity adjustment of `-log10(C(N, 2))`. At *N* = 2 this is zero.
2. **The score ceiling.** `L_match = -log10(p_match + epsilon)`, so no run can
   emit a score above `-log10(epsilon)` — 15.0 at the default epsilon of
   1e-15. A pair with 17.2 decades of contributions reports 15.0.

Every evidence page therefore carries a **reconciliation row**: contributions
total, anchor adjustment, stored score. When a run manifest supplies `epsilon`
the page also names the ceiling, which is what turns an apparent arithmetic
failure into a stated property of the computation.

`epsilon` and the `L_match` zone boundaries are **not** in a saved run's
features file. They live on `PipelineConfig` and `DecisionThresholds`, and are
recorded only in a `RunManifest`. So:

```python
from llm_dup_dev.review_html import load_evidence_run_settings

settings = load_evidence_run_settings("run_manifest.yaml")   # or .json
```

With settings, the review quotes the run's own numbers. Without them, it names
the evidence zone qualitatively and quotes no threshold and no ceiling. It
never reconstructs them from current defaults — a review that quoted today's
thresholds for a run from six months ago would be describing a run that never
happened. Supply `run_manifest` for every dataset in a collection you intend
to publish.

---

## Selecting pairs

Three ways to choose what gets rendered, in increasing order of deliberation:
every pair in the artifact, an explicit list of ids, or a curated manifest.

```python
from llm_dup_dev.review_html import PairSelection, MissingPairPolicy, render_saved_run

render_saved_run(
    reports_path="reports.json",
    pairs_path="pairs.json",
    pair_results_path="pair_results.json",
    select=["R001__R002", "R001__R003"],      # or a PairSelection
    review_format="evidence",
    output_dir="outputs/evidence/",
)
```

`select` accepts a `PairSelection` or a bare iterable of ids, and is applied
before any per-pair work. `select=None` behaves exactly as before.

Two failure modes that look similar and are not:

- **An id matching no pair in the artifact raises, regardless of `strict`.**
  That is a mistake in the request, not a defect in the data. Every unknown id
  is reported at once, so a stale manifest costs one error rather than three
  edit-and-retry cycles.
- **A *selected* pair that cannot be loaded** follows the selection's own
  `on_missing` policy, which defaults to `ERROR`. This is a genuine semantic
  fork from `strict`: `strict=False` warns and skips an unselected pair, but a
  pair someone named explicitly still raises. Silently dropping it would leave
  a curated collection quietly short of an example, which is the one failure
  mode a collection cannot detect for itself.

`PairSelection.preserve_order` defaults to `True`: selection order is the
output order, because a curated sequence is a deliberate choice.

### Pairs from before canonicalisation

`load_saved_run` validates the whole pairs artifact, so selecting three good
pairs out of a pre-canonicalisation file still fails. Set
`allow_noncanonical_pairs: true` on the dataset (or pass
`--allow-noncanonical-pairs`) to view it as written. That is a visible choice
in the manifest rather than a render-time surprise; anything feeding an
evaluation must be regenerated instead.

---

## Curated collections

A collection manifest names the datasets to read and the examples to show, in
order, with the editorial framing that belongs in a file rather than on a
command line. YAML or JSON, by file suffix; YAML is read with `safe_load`
only. See [`examples/evidence_collection_example.yaml`](../examples/evidence_collection_example.yaml)
for a complete synthetic one — its `datasets` paths name a saved run that is
not committed here, since run artifacts are data files and this repository
holds none, so copy the manifest and point it at your own.

```yaml
schema_version: "1.0"
title: Illustrative report pairs from the performance evaluation of vigiTwin
intro: >
  Two examples illustrating how contextual matching evidence and retained
  inconsistencies inform the two-stage adjudication.
note: >
  Draft for author review. This is an internal evidence review.
positive_class: primary

datasets:
  synthetic_set:
    label: Synthetic set
    reports: synthetic_run/reports.json
    pairs: synthetic_run/pairs.json
    pair_results: synthetic_run/pair_results.json
    pair_frequencies: synthetic_run/pair_frequencies.json
    run_manifest: synthetic_run/run_manifest.json

examples:
  - example_id: S1
    dataset: synthetic_set
    pair_id: R001__R002
    title: Duplicates with extensive shared information
    lede: Extensive matching evidence despite differences in coding.
    scle_assessment:
      state: not_entered
    case_interpretation: >
      Human-authored interpretation goes here.
    review_status: draft
```

### Fields

**Collection:** `schema_version`, `title`, `intro`, `note`, `positive_class`,
`datasets`, `examples`.

**Dataset** (`datasets.<dataset_id>`): `label` (the kicker shown on each
example from this set), `reports` and `pairs` (required), then either
`pair_results` **or** both `features` and `classifications` — the same
precedence `load_saved_run` applies, stated once in the manifest instead of
discovered at load time. Optional: `pair_frequencies`, `screening`,
`annotations`, `narrative_demographics`, `run_manifest`,
`allow_noncanonical_pairs`.

**Example:** `dataset` and `pair_id` (required), `example_id` (also the output
filename), `title`, `lede`, `kicker`, `scle_assessment`, `case_interpretation`,
`summary_mode`, `review_status`, `notes`.

**`scle_assessment`:** `state` is one of `not_entered`, `not_assessed`,
`assessed`, `inconclusive` — four states, because three different absences
mean different things and a reader must not have to guess which applies.
`issue` is a `ReferenceLabelIssue` value, reused from
`study_eval.scle.form` rather than re-declared. `assessed` requires one and
rejects `genuinely_ambiguous` — that is the `inconclusive` state, and the
error says so. `inconclusive` takes `genuinely_ambiguous` or nothing at all,
filling it in either way. The two "no assessment" states reject an issue
outright, since there was no assessment to have an opinion.

**`positive_class`** names the rule used when comparing a binary reference
label against vigiTwin's four-label output: `primary` (only `Duplicates`
counts as positive), `abstain_positive`, or `otherwise_related_positive`.
These mirror `study_eval.aggregate`'s prespecified rules, which are a
*study-protocol* choice rather than a property of the method — so the page
footnotes the rule whenever it is not the primary one. Comparison happens in
the binary space the reference label actually lives in; `abstain` on either
side yields no verdict at all rather than a disagreement.

### What the manifest may not say

Every model is `extra="forbid"`, and that is load-bearing rather than
fastidious: **labels come from the artifacts, never the manifest.** A stray
`reference_label:` or `final_label:` key is a hard error, not a silently
ignored one, so a collection cannot assert a verdict the saved run does not
support.

Validated on load, with every problem reported at once: unique dataset and
example ids, resolvable dataset references, no repeated pairs, the artifact
XOR above, SCLE state/issue consistency, and **filename collisions after
sanitisation** — two example ids differing only in punctuation would collapse
to one output file.

Relative paths resolve against the manifest's own directory, so a collection
and its artifacts move together.

> A real collection for a manuscript carries original report identifiers and
> clinical interpretations, so it belongs outside this public repository. Only
> synthetic manifests and synthetic reports are committed here.

---

## Gaps

Anything the stored artifacts cannot support becomes a visible footnote and a
test-assertable record, never a silent omission. An evidence review that
quietly drops what it could not compute is indistinguishable from one where
there was nothing to show.

| Code | Means |
|---|---|
| `drug_set_terms_missing` | A drug-set inconsistency carries no `terms`, so the offending substances cannot be highlighted individually. Written by releases before 1.1.0. |
| `clinical_date_value_missing` | A clinical-date contribution carries no `matched_value`, so the matched date cannot be attributed to a specific field. Likewise pre-1.1.0. |
| `unmapped_inconsistency` | An inconsistency whose `feature` this format does not know how to attach to a report element. It is still listed in section 3. |
| `unknown_entity_label` | A clinical-date finding whose `entity_label` does not parse into one of the four known forms. |
| `run_settings_missing` | No run manifest was supplied, so thresholds and the score ceiling are not quoted. |
| `frequency_stats_missing` | No frozen pair frequencies, so the frequency table is absent. |
| `no_features` / `no_classification` | The pair's assessment was not saved. It renders as a report comparison with everything neutral — not with colours invented from a comparison this format does not perform. |
| `score_unreconciled` | Contributions and stored score disagree by more than the tolerance, with no epsilon available to explain it. |

Older features files load and render with the first two gaps as page
footnotes. Both are absences of recorded detail, not errors.

---

## What this format is not

**It is not publication-safe.** An evidence review may carry original report
identifiers, country names and narrative text, and its footer says so. Omitting
the debug panels does not make a page anonymised, and nothing here should be
described as publication-ready.

A later phase adds the confidentiality transformations — date and age
offsets, country masking, identifier replacement, narrative redaction — and
the publication export built on them. This phase is designed to *accommodate*
that and deliberately does not implement it.

The design commitment that makes it possible: after the builder returns, the
only report value anywhere in the evidence view model is `EvidenceCell.text`.
Attribution records *why* a cell is coloured — feature name, severity,
contribution in decades — and never a source value, so it is non-identifying
and a publication transform is a pure `text` rewrite. This is pinned by a test
that shifts every date in a case by a shared offset and asserts the evidence
tree is byte-identical once the `text` fields are stripped.

---

## Entry points

### Python

```python
from llm_dup_dev.review_html import (
    build_evidence_case,          # one pair -> EvidenceCaseVM
    build_assessment_summary,     # the deterministic prose
    build_collection,             # a loaded manifest -> EvidencePageVM
    load_curated_manifest,        # YAML/JSON manifest + path resolution
    load_evidence_run_settings,   # thresholds and epsilon from a run manifest
    write_evidence_html,          # one case -> one file
    write_evidence_batch_html,    # many cases -> files + index
)
```

`build_evidence_case` takes `report_1`, `report_2`, `pair_id` and optionally
`features`, `classification`, `frozen_bundle`, the two narrative-demographics
extractions, a `ReferenceInfo` (reference label, set name, screening outcome,
SCLE state, positive-class rule), an `ExamplePresentation` (the curated
framing) and `EvidenceRunSettings`.

### A whole saved run, or a selection from one

```bash
.venv/bin/python -m llm_dup_dev.review_html \
    --reports reports.json --pairs pairs.json \
    --features features.json --classifications classifications.json \
    --pair-frequencies pair_frequencies.json --run-manifest run_manifest.json \
    --format evidence --set-label "Synthetic set" \
    --output-dir outputs/evidence/
```

The CLI reads the split `features.json` / `classifications.json` artifacts; a
combined `pair_results.json` is read by `load_saved_run` and by a collection
manifest's `pair_results`, not by this CLI.

`--format` defaults to `detailed`, so every existing invocation is unchanged.
`--pair-ids ID [ID ...]` narrows to a selection in the order given.
`--run-manifest` and `--set-label` apply to the evidence format only. Output
goes to `outputs/evidence_review/` by default, not to the detailed format's
`outputs/review_html/`: both formats write an `index.html` and name a pair's
page the same way, so one directory cannot hold both.

### A curated collection

```bash
.venv/bin/python -m llm_dup_dev.review_html.evidence \
    --manifest collection.yaml \
    --output-dir outputs/supplement/
```

Also takes `--no-index`, `--single-document PATH` (every example in one file),
`--export-drafts PATH` with `--force`, and `--skip-missing` (off by default:
a curated collection quietly missing an example is worse than one that does
not build). Recorded gaps print to stderr.
