# Duplicate Adjudication Method Specification

This document specifies the method used by the **duplicate adjudication
pipeline** to classify candidate report pairs that have already been flagged
as suspected duplicates by an upstream candidate-generation method (e.g.
vigiMatch). For each pair the pipeline produces a final classification of
**Duplicates**, **Otherwise related**, **Abstain**, or **Unrelated**.

The method is primarily a transparent rule-based decision logic operating on
structured features, with optional LLM-assisted analysis of report narratives.
It draws on two complementary kinds of evidence:

1. **Rarity-based matching evidence**, which quantifies how unlikely the
   observed combination of matched features would be by chance within a
   relevant comparison population (the *anchor population*). This drives the
   **independence assessment**.
2. **Inconsistency signals**, which capture structured or narrative
   information that argues against the reports describing the same case. These
   drive the **relationship assessment** once independence has been rejected.

The probabilistic component is framed as a test of the **independence
null hypothesis**: it estimates how surprising the matched features would
be if the two reports were independent draws from the comparison
population. A small expected number of collisions is evidence *against*
independence, but it is not specifically evidence that the two reports
describe the same individual: same reporter, same study, copies of the
same case routed through different channels, or unusual shared context
can all produce a small expected-collision count without the reports
describing the same patient. The pipeline therefore separates an
**independence assessment** (driven by the matching evidence, § Independence
assessment) from a **relationship assessment** (driven by the inconsistency
signals, § Relationship assessment). The next section summarizes how the two
combine into the final user-facing label; the two assessment chapters give the
full rules.

## Overview

For each pair, the algorithm dispatches between two paths:

- **Shared-source override.** If narrative extraction surfaces a
  reliable shared source identifier (DOI, PMID, trial registration,
  …) or a shared multi-patient context, the pair cannot be treated as
  independent regardless of the rarity-based evidence. A small set of
  per-verdict rules then maps the pair to `Duplicates`,
  `Otherwise related`, or `Abstain`.
- **Rarity-based zone logic.** Otherwise, the matching evidence
  \(L_{\text{match}}\) selects one of four decision zones (strong /
  moderate / weak / below threshold), and the weighted inconsistency
  score \(w_{\text{tot}}\) is compared against the zone's tolerance
  to choose between `Duplicates`, `Otherwise related`, and
  `Unrelated`.

The tolerance for inconsistencies **scales with the strength of
matching evidence**: in the compelling zone the inconsistency budget
(`w_tot < 4`) accommodates a single strong inconsistency
(`w_tot = 3`), while the intermediate (`w_tot < 3`) and limited
(`w_tot < 1`) zones reject it. The principle: highly improbable
matched features make occasional recording errors a more plausible
explanation than coincidence.

**Decisive inconsistencies** (e.g. an impossible clinical-date
relationship, or distinct case identifiers under the same shared
source) participate in \(w_{\text{tot}}\) with a deliberately large
weight that exceeds every zone tolerance, so any decisive count
precludes `Duplicates`. They do not by themselves overturn the
independence assessment, so a pair with decisive inconsistencies and
adequate matching evidence still lands in `Otherwise related` rather
than `Unrelated`.

### Final label mapping

The two internal layers — `Non-independent / Independent` from the
matching evidence, and `Duplicates / Otherwise related / Abstain`
from the inconsistency signals once independence has been rejected —
map to the user-facing label vocabulary as follows:

| Independence assessment | Duplicate relationship | User-facing final label |
|---|---|---|
| Non-independent | Duplicates | **Duplicates** |
| Non-independent | Otherwise related | **Otherwise related** |
| Non-independent | Abstain | **Abstain** |
| Independent | — | **Unrelated** |

This mapping is implemented by `ClassificationResult.final_label` and
is the only label that downstream consumers (eval, review HTML,
secure repo) should rely on.

The **Abstain** label is not generic uncertainty. It represents a
specific epistemic state: non-independence is established (either a
reliable shared source identifier or a shared multi-patient context
is present), but the available structured evidence is insufficient to
safely determine whether the two reports describe the same
individual. It is produced exclusively by the shared-source override
path — covering both the `SAME_SOURCE_*` verdicts and the
high-\(L_{\text{match}}\) branch of `MULTI_PATIENT_CONTEXT_NO_LINK` —
for scenarios where latent unavailable information (e.g. the full
publication text, in-house patient identifiers) may be required to
resolve the question definitively. See § Relationship assessment for
per-verdict details.

## Evidence construction

For each report pair the pipeline first constructs report-level and
pair-level evidence from structured fields, normalized values, and (optionally)
LLM-extracted narrative content. All pairwise feature construction is
deterministic; the only non-deterministic step is the per-report LLM
extraction, whose output can be frozen and reused.

### Structured data and normalization

Subsequent comparisons operate on **normalized** values. Drug substance names
are compared case-insensitively; adverse events are compared as normalized
MedDRA Preferred Terms (PTs) with their primary HLT / HLGT / SOC hierarchy;
partial dates are represented as intervals spanning their earliest and latest
plausible calendar dates. **Missing information never in itself generates
either matching evidence or an inconsistency** — the method distinguishes
absence of evidence from observed agreement or disagreement.

### Narrative-derived features

The pipeline includes an optional LLM-based per-report extraction step that pulls structured information from each report's free-text narrative. The extracted values are appended to the report's existing structured fields (where applicable) and consumed by downstream matching, inconsistency, and override logic.

The extraction is **per-report and pair-independent** — each report's narrative is processed once and the result can be frozen as JSON for offline reuse across many pair comparisons. When no LLM client is configured the pipeline falls back to the report's structured fields alone.

Two per-report extractors run:

#### Demographic candidates (`narrative_demographics`)

The `narrative_demographics` LLM task extracts candidate **age**, **sex**, **weight**, and **height** values from the narrative, each accompanied by a verbatim evidence snippet. The extractor is deliberately conservative — qualitative descriptors ("adult", "elderly", "obese", "tall") never yield a value. Extracted values are appended to the corresponding `patient_*_values` lists on `Report` with `sources=["narrative_llm"]`, so the downstream demographic inconsistency checks (see § Inconsistency signals) and the demographic-feature contributions to \(L_{\text{match}}\) (see § Probability estimation) operate over the combined structured + narrative candidate set under their normal rules — no separate plumbing on the consuming side. When there are multiple candidate values for the same feature, it is sufficient for **any** value to match a value on the other report to count as a match.

#### Source and case identifiers (`narrative_source_case`)

The `narrative_source_case` LLM task extracts:

- a list of **typed source identifiers** (`doi`, `pmid`, `trial_registration`, `study_name`, `protocol_number`, `linked_case`, `article_reference`), each post-normalized by the type-specific normalizer in `feature_generation/source_case_comparison.py`;
- the booleans **`single_patient_source`** and **`multi_patient_source`** that the narrative supports;
- an optional **case-within-source identifier** with a typed family (`case` / `patient` / `pt` / `subject`).

The extractor is conservative: fields are null whenever the narrative does not give clear support. The per-pair deterministic comparison that consumes these records and emits a verdict is described under § Independence assessment (shared-source detection).

> *(Per-pair LLM components: the AE clinical-picture assessor runs at pair level rather than per report, and is described under § AE clinical-picture inconsistency.)*

## Independence assessment

The independence assessment decides whether the two reports may be altogether
independent draws from the comparison population. Independence is rejected in
either of two ways: a **reliable shared source identifier** establishes
non-independence directly, or, in its absence, the **rarity-based matching
evidence** \(L_{\text{match}}\) exceeds the limited-zone threshold. A pair for
which neither holds is classified **Unrelated** and not processed further.

### Shared-source detection

A reliable shared source identifier derived from the narratives is itself evidence against independence: if two reports both quote the same DOI (or other stable source identifier), they cannot be treated as independent draws from the comparison population regardless of how rare the matched clinical features are. This case is handled through a dedicated override path in `classifier/decision_logic.py`, separate from the rarity-based \(L_{\text{match}}\) framework. The verdict is computed upstream by `feature_generation/source_case_comparison.compare_source_case`, deterministically (no LLM), from the two per-report `NarrativeSourceCase` records described in § Narrative-derived features. Full schemas live in [`data_contracts.md § Derived Narrative Signals`](data_contracts.md).

The logic has two parts:

1. **Shared-source detection.** Each side is reduced to a set of `(type, normalized_value)` tuples. If those sets intersect on at least one non-`unknown` type, the most specific shared type (DOI > PMID > trial registration > linked case > protocol number > study name > article reference) is surfaced as `shared_source_identifier`. When strict equality finds nothing, a conservative token-set fallback is tried over `ARTICLE_REFERENCE` pairs only — citations that differ only in punctuation, accents, or supplement / page-range abbreviation style (e.g. `34(2):S136-S137` vs `34 (Suppl 2):S136-7`) are recovered when their lowercase NFKD-folded alphanumeric tokens, minus a small noise vocabulary (`et`, `al`, `vol`, `suppl`, `no`, `iss`, `pp`, …), share Jaccard ≥ 0.85, ≥ 8 non-noise tokens, and an equal 4-digit year when both sides carry one. Strict DOI/PMID/etc. matches always win over this fallback by virtue of the preference order.

2. **Verdict selection.** Given the outcome of step 1 and the `single_patient_source` / `multi_patient_source` / case-identifier signals on each side, exactly one verdict is emitted:

    | Pre-condition | Verdict |
    |---|---|
    | Either extraction missing | `NOT_ASSESSED` |
    | Shared source identifier found, and either side has `single_patient_source=True` | `SAME_SOURCE_SINGLE_PATIENT` |
    | Shared source identifier found, comparable case identifiers exist on both sides, normalized values **agree** | `SAME_SOURCE_SAME_CASE` |
    | Shared source identifier found, comparable case identifiers exist on both sides, normalized values **differ** | `SAME_SOURCE_DIFFERENT_CASE` |
    | Shared source identifier found, but case identifiers are missing on one side, of incompatible family, or otherwise not safely comparable | `SAME_SOURCE_UNCOMPARABLE_CASE` |
    | No shared source identifier, at least one side reports `multi_patient_source=True`, and neither side reports `single_patient_source=True` | `MULTI_PATIENT_CONTEXT_NO_LINK` |
    | Otherwise | `NO_DECISIVE_SIGNAL` |

    Two case identifiers are considered *comparable* iff both carry a non-null non-`unknown` family of the same value (`case` vs `case`, `patient` vs `patient`, …) and both have non-empty normalized values; the comparator never treats family-mismatched or family-missing identifiers as evidence of different individuals.

The verdict, the surfaced `shared_source_identifier` (with its type), and short prose `reasoning` are stored on `PairFeatures.source_case_assessment`. Any `SAME_SOURCE_*` verdict establishes non-independence; the four verdicts' **sub-label** rules are given in § Relationship assessment. `MULTI_PATIENT_CONTEXT_NO_LINK` is *partially* advisory: it establishes non-independence and routes to the two-way sub-label rule only when \(L_{\text{match}} >\) `lmatch_limited`, and otherwise falls through to the ordinary rarity-based zone logic (→ `Independent`).

### Rarity-based matching evidence

The probabilistic framework evaluates how surprising the observed combination of matched features would be if the two reports originated from different individuals within the anchor population.

Intuitively, if many reports exist within the anchor population that share similar drugs and adverse events, then additional matches (such as clinical dates or patient characteristics) may occur by chance. Conversely, if the probability of observing the full set of matched features is extremely small, the reports are unlikely to describe different individuals.

The evidential value of matching report attributes depends on the population within which random matches may occur. This population is defined by the **reporting context** and the **anchor population**.

#### Reporting context

The reporting context defines the subset of the database within which probabilities and frequencies are evaluated.

If the two reports share the same reporting country and reporting year, the preferred context is:

$$
\text{context} = \text{country} + \text{year}
$$

If the reports originate from different countries or years, this context cannot be applied. In such cases a broader context is used, typically:

$$
\text{context} = \text{country}
$$

or, if necessary:

$$
\text{context} = \text{global database}
$$

Using the most specific shared context ensures that probability estimates reflect the reporting patterns relevant to the report pair.

#### Matching clinical feature sets

For each report pair, the following sets of shared clinical features are determined:

```
matching_drugs
matching_AEs
```

Each set may contain one or more elements. Drug overlap is **role-agnostic** (a substance matches even when its role differs between reports); AE overlap is based on exact normalized PT identity.

Example:

```
matching_drugs = {sertraline, rosuvastatin}
matching_AEs = {rash, pruritus}
```

#### Candidate anchor populations

Within the reporting context, three candidate populations are computed:

```
N_drugs = number of reports containing all matching drugs
N_AEs = number of reports containing all matching adverse events
N_drugs+AEs = number of reports containing both
```

These populations represent progressively narrower clinical definitions.

These counts **include the two focal reports**. A non-empty matching set therefore forces its count to at least 2 — both reports contain what they were found to share. (The leave-pair-out marginals rely on the same fact, treating \(n_f > 2\) as evidence outside the focal pair.)

It follows that a count of zero can only mean **the matching set is empty**, never that the population is empty. Since every report vacuously contains the empty set, an empty matching set leaves the population **unnarrowed**, not empty: the corresponding candidate is the whole reporting context, \(N_{\text{total}}\).

#### Anchor selection

The anchor population defines the baseline population within which collision probabilities are evaluated.

Let \(T\) denote a configurable threshold (e.g. \(T = 50\)).

The anchor is selected as follows:

0. If either \(N_{\text{drugs}}\) or \(N_{\text{AEs}}\) is zero, that matching set is empty and so imposes no restriction; the candidate is skipped and the other is used. If **both** are zero, neither set narrows the population and the anchor is the whole reporting context, \(N_{\text{anchor}} = N_{\text{total}}\).

1. If either \(N_{\text{drugs}}\) or \(N_{\text{AEs}}\) is less than or equal to \(T\), select the smaller of the two.

2. Otherwise, use the intersection:

$$
\text{anchor} = \text{drugs} \cap \text{AEs}
$$

The size of the selected population is \(N_{\text{anchor}}\).

A small anchor population indicates that the shared clinical context is rare, meaning that even modest additional matches may provide strong evidence that the reports describe the same patient. Conversely, large anchor populations imply that many similar reports exist, requiring stronger supporting evidence to establish duplication. The limiting case is a pair sharing no drugs and no adverse events: the anchor is the entire reporting context, so the multiplicity \(M = \binom{N_{\text{anchor}}}{2}\) is at its largest and any remaining matches must be correspondingly more surprising to carry weight.

Note that \(N_{\text{anchor}}\) is never zero, and this matters to the method's validity rather than merely to its numerics. A zero anchor would make \(M = 0\), so \(P_{\text{match}} = 0\) would hold **vacuously** — not because a chance collision is improbable, but because no pair exists to collide. Since \(L_{\text{match}} = -\log_{10}(P_{\text{match}} + \varepsilon)\) reads a small \(P_{\text{match}}\) as strong evidence, a vacuous zero would saturate \(L_{\text{match}}\) at its ceiling and report two reports with *nothing in common* as maximally compelling evidence of duplication — the exact inverse of the truth. Treating an empty matching set as unnarrowed rather than empty removes this case by construction.

### Probability estimation

After identifying matching features between the two reports, the evidential strength of the match is evaluated by estimating how likely the **combined pattern of matched features** would be to occur by chance within the anchor population.

The features that contribute to the composite matching probability are:

- **Drug set** — when not part of the anchor definition (leave-pair-out marginal at the narrowest scope with extra-pair evidence; primary → country → global → floor)
- **AE set** — when not part of the anchor definition (leave-pair-out marginal at the narrowest scope with extra-pair evidence; primary → country → global → floor)
- **Patient demographics** — age, sex, height, weight (domain-size based)
- **Clinical dates** — unique dates matching across drug start/end, AE onset, and overall AE onset fields (domain-size based)

Note: **report date is excluded** from the probability computation. It is matched for evidence display purposes only, as it primarily reflects administrative timing rather than patient-identifying clinical information.

The framework proceeds in three steps: (1) determine a feature-level probability \(p_f\) for each matched feature; (2) combine these probabilities across features; (3) estimate the expected number of such combined matches within the anchor population.

#### Feature-level probability

For each matched feature \(f\), define a probability \(p_f\) representing the probability that a randomly selected report from the relevant reporting context would match that feature value.

$$
p_f =
\begin{cases}
\text{empirical relative frequency}, & \text{if available} \\
1/D_f, & \text{otherwise}
\end{cases}
$$

where \(D_f\) is the number of plausible distinct values for feature \(f\).

Empirical frequencies are preferred when reliable frequency estimates are available (e.g., for drug sets and AE sets). Otherwise, the probability is approximated using the inverse of the plausible domain size.

#### Composite matching probability

If the matched features are \(f_1, f_2, \dots, f_k\), their probabilities are combined multiplicatively:

$$
p_{\text{joint}} = \prod_{i=1}^{k} p_{f_i}
$$

Equivalently, computation can be performed in log space:

$$
\log p_{\text{joint}} = \sum_{i=1}^{k} \log p_{f_i}
$$

This formulation allows multiple individually weak matches to accumulate into strong evidence when their joint occurrence is unlikely.

#### Multiplicity-adjusted chance-match probability

The quantity \(p_{\text{joint}}\) describes a **single** pairwise opportunity. The anchor population, however, offers many opportunities for a chance match, and the method must account for that multiplicity.

Given an anchor population of size \(N_{\text{anchor}}\), the number of unordered report pairs is:

$$
M = \binom{N_{\text{anchor}}}{2}
= \frac{N_{\text{anchor}}(N_{\text{anchor}}-1)}{2}
$$

Treating each of these \(M\) pairs as an **equivalent and independent** opportunity for a chance match with per-pair probability \(p_{\text{joint}}\), the probability that **at least one** such collision occurs somewhere in the anchor population is:

$$
P_{\text{match}} = 1-(1-p_{\text{joint}})^{M}
$$

This is the quantity the method uses. It is bounded in \([0, 1]\) by construction: however many opportunities the anchor provides, the probability that some pair collides cannot exceed certainty.

**Assumptions.** The expression is an analytical approximation, not an exact real-world probability. It assumes the \(M\) pairwise opportunities are mutually independent and that each carries the same chance-match probability \(p_{\text{joint}}\). Neither holds exactly: reports within an anchor population are not independent draws, and \(p_{\text{joint}}\) is itself estimated. \(P_{\text{match}}\) should therefore be read as a calibrated measure of how unsurprising the observed match is given the size of the anchor — not as a literal frequency.

**Relation to the expected count.** The expected number of chance collisions is \(E_{\text{joint}} = M\,p_{\text{joint}}\). This is an unbounded count rather than a probability, and it is not used by the method. It relates to \(P_{\text{match}}\) as an upper bound, \(P_{\text{match}} \le E_{\text{joint}}\) (union bound), with the two converging as \(E_{\text{joint}} \to 0\). The Poisson form \(1-\exp(-E_{\text{joint}})\) is likewise a rare-event approximation only; the exact expression above defines the method.

**Numerical evaluation.** \(P_{\text{match}}\) is computed as \(-\operatorname{expm1}\!\left(M \cdot \operatorname{log1p}(-p_{\text{joint}})\right)\). Evaluating \(1-(1-p_{\text{joint}})^{M}\) directly underflows to exactly zero for the small \(p_{\text{joint}}\) this method routinely produces, which would misreport negligible evidence as maximal.

#### Composite matching evidence

The overall matching evidence is then defined as:

$$
L_{\text{match}} =
\max\left(0,\,-\log_{10}(P_{\text{match}}+\varepsilon)\right)
$$

where \(\varepsilon\) is a small constant used to avoid numerical instability. Because \(\varepsilon\) is additive inside the logarithm, an exactly zero \(P_{\text{match}}\) — which arises when \(M = 0\), i.e. an anchor too small to furnish any comparison — yields \(L_{\text{match}} = -\log_{10}\varepsilon\) rather than an infinite value.

\(L_{\text{match}}\) is a **multiplicity-adjusted chance-match measure**: it increases as the probability of any chance collision anywhere in the anchor population decreases, so rarer feature combinations produce stronger evidence, and a larger anchor population weakens the evidence from an identical match. It is **not** a p-value, a hypothesis-test result, or a tail probability, and should not be reported or interpreted as one.

This framework evaluates how surprising the **entire pattern of observed matches** would be within the anchor population: individually weak matches may accumulate into strong evidence when their joint probability is sufficiently small, while matches that are common within the anchor population contribute little or no evidence.

### Determining domain size \(D_f\)

For features without empirical frequency estimates, the domain size \(D_f\) represents the number of plausible values for the feature at the observed resolution.

The domain size depends on:

- the feature type
- the resolution of the matched value
- the clinically plausible value range
- the temporal context when applicable

When the two reports record a numeric demographic at different resolutions (e.g. `75.2 kg` and `75 kg`), the values are compared at the **coarser** of the two resolutions: each is rounded to the nearest point on the coarser grid (the bin \([v - r/2,\, v + r/2)\)) and they match when they fall in the same bin. The domain size is then taken at that coarser matched resolution. An exact match at a finer resolution (`75.2 kg` ↔ `75.2 kg`) is retained at the finer resolution and earns the correspondingly larger domain; where several candidate values are present, the finest matching pair is credited. The same rule applies to age, height, and weight.

**Age.** Age domain size depends on the resolution of the recorded age.

| Representation    | Example  | Domain size                                                   |
| ----------------- | -------- | ------------------------------------------------------------- |
| Age in years      | 54       | plausible ages 0–80 → \(D_f = 80\)                           |
| Age in months     | 7 months | plausible infant months → \(D_f = 100\)                      |
| Age with decimals | 54.2     | \(D_f = 80 / \text{resolution}\) (e.g. 0.1 years → \(D_f = 800\)) |

**Sex.** Sex is treated as a categorical variable, \(D_f = \text{number of possible categories}\). In the simplest model (we are aware that a better approach would account for the differing relative frequencies of male and female, but in practice matching patient sex carries very little evidentiary value either way), \(D_f = 2\).

**Height.** For height, the domain size depends on the patient's age group and the measurement resolution. Children are defined as patients aged ≤15 years.

| Age group         | Resolution | Domain size      |
| ----------------- | ---------- | ---------------- |
| Child (≤15 years) | 1 cm       | \(D_f = 20\)     |
| Adult (>15 years) | 1 cm       | \(D_f = 40\)     |
| Child (≤15 years) | 0.1 cm     | \(D_f = 200\)    |
| Adult (>15 years) | 0.1 cm     | \(D_f = 400\)    |

**Weight.** Weight is handled analogously to height.

| Age group         | Resolution | Domain size      |
| ----------------- | ---------- | ---------------- |
| Child (≤15 years) | 1 kg       | \(D_f = 20\)     |
| Adult (>15 years) | 1 kg       | \(D_f = 40\)     |
| Child (≤15 years) | 0.1 kg     | \(D_f = 200\)    |
| Adult (>15 years) | 0.1 kg     | \(D_f = 400\)    |

For height and weight, higher measurement precision increases domain size and therefore increases the evidential value of a match at that resolution.

#### Drug sets and adverse event sets

For drug sets and adverse event sets, the probability is estimated as a **leave-pair-out marginal** of the feature, evaluated at the narrowest reporting scope that contains evidence of the feature *outside* the two focal reports. The scopes are tried in order — **primary → country → global** — and a per-report **floor** is used only when no scope has any extra-pair occurrence. This avoids self-inclusion bias from the two focal reports and prevents zero-probability pathologies when the focal pair is the only occurrence of the feature in the primary context.

Drug sets and AE sets are evaluated only when they are **not part of the anchor definition**, to avoid double-counting anchor information.

*Leave-pair-out ratio.* For a given scope with \(n_{\text{total}}\) reports of which \(n_{\text{feature}}\) match the feature, the leave-pair-out marginal is the bare ratio after removing the two focal reports from both numerator and denominator:

$$
\hat{p} \;=\; \frac{n_{\text{feature}} - 2}{n_{\text{total}} - 2}
$$

This is defined and informative only when \(n_{\text{feature}} > 2\) (i.e. at least one report outside the focal pair carries the feature) and \(n_{\text{total}} > 2\).

*First-level-with-evidence cascade.* Walk the scopes from narrowest to broadest and return the bare LPO ratio at the **first scope where the feature has any extra-pair evidence**:

1. **Primary** (the reporting context determined for the pair): if \(n_{\text{feature}} > 2\) and \(n_{\text{total}} > 2\), use \(p_F = (n_{\text{feature}} - 2) / (n_{\text{total}} - 2)\).
2. **Country** (when country counts are supplied and primary had no extra-pair evidence): if \(N_{\text{feature}}^{\text{country}} > 2\) and \(N_{\text{total}}^{\text{country}} > 2\), use \(p_F = (N_{\text{feature}}^{\text{country}} - 2) / (N_{\text{total}}^{\text{country}} - 2)\).
3. **Global** (when global counts are supplied and country had no extra-pair evidence): if \(N_{\text{feature}}^{\text{global}} > 2\) and \(N_{\text{total}}^{\text{global}} > 2\), use \(p_F = (N_{\text{feature}}^{\text{global}} - 2) / (N_{\text{total}}^{\text{global}} - 2)\).
4. **Floor** (no scope has extra-pair evidence): \(p_F = 1 / \max(N_{\text{total}}^{\text{global}} - 2,\; 1)\) when the global total is available, else \(p_F = 10^{-6}\) (`MARGINAL_FLOOR`).

Scopes whose counts are not supplied by the data layer are skipped. The \(-2\) leave-pair-out subtraction is applied at whichever scope is used: the focal pair sits inside country-year ⊂ country ⊂ global, so it is removed from numerator and denominator at whatever level the cascade lands on. The first scope tried is the pair's **reporting context** (country-year, country, or global); it equals a country-year scope only when that is the context.

*Design rationale.*

- **Leave-pair-out at every used scope** prevents the focal pair from inflating its own evidence.
- **Bare LPO ratio (no Bayesian smoothing)** is the natural estimate once extra-pair evidence exists at some scope. An earlier recursive Bayesian-smoothed ladder pulled each level's posterior toward the level below by a factor \(\alpha / (n_{\text{total}} + \alpha)\); for a feature unique to the focal pair globally this drove the estimate to \(\sim 10^{-16}\), ten orders of magnitude **below** the intended per-report floor — over-weighting the feature as duplicate evidence (\(+16\) to \(L_{\text{match}}\) instead of \(+6\)).
- **Narrow-first cascade** uses the most specific evidence available: country-year primary rates are the right unit for the duplicate-pair likelihood; broader scopes are consulted only when primary is silent.
- **The floor** guarantees a strictly positive probability when no scope contains evidence outside the focal pair. With the global-derived floor, a feature seen only in the focal pair lands at roughly the per-report rate of the database (\(\sim 1/N_{\text{total}}^{\text{global}}\)); with the constant fallback it lands at \(10^{-6}\), contributing roughly 6 to \(L_{\text{match}}\) instead of vanishing.

*Worked example.* Suppose the matching drug set is rare, the focal pair is the only country-year occurrence, and the data layer supplies broader-context counts:

- Primary (country-year): \(N_{\text{drugs}} = 2\), \(N_{\text{total}} = 1{,}000\) (only the focal pair has these drugs in this country-year).
- Country: \(N_{\text{drugs}}^{\text{country}} = 5\), \(N_{\text{total}}^{\text{country}} = 10{,}000\).
- Global: \(N_{\text{drugs}}^{\text{global}} = 80\), \(N_{\text{total}}^{\text{global}} = 1{,}000{,}000\).

Walking the cascade: Primary \(N_{\text{drugs}} = 2\) → no extra-pair evidence, skip. Country \(N_{\text{drugs}}^{\text{country}} = 5 > 2\), so

$$
p_F = \frac{5 - 2}{10{,}000 - 2} \;\approx\; 3.0 \times 10^{-4}
$$

The country scope captures the fact that the drug set is rare but not vanishingly so; the global scope is never consulted. If unique at the country level too (\(N_{\text{drugs}}^{\text{country}} = 2\)) but observed 80 times globally, the cascade would fall through to global and yield \((80-2)/(1{,}000{,}000-2) \approx 7.8 \times 10^{-5}\). If unique at every scope, it would land at the floor \(1/(1{,}000{,}000 - 2) \approx 1.0 \times 10^{-6}\) — never below.

This logic applies symmetrically to the AE set (with the AE-set counts at each level) and the same formula is used regardless of which of drugs / AEs forms the anchor.

#### Clinical dates (drug start/end, AE onset, overall AE onset)

Matching dates from drug, adverse event, and overall AE onset records provide additional evidence for the collision probability. This uses **explicit date-field coincidence** only — treatment intervals are NOT expanded into sets of intermediate dates.

*Which dates are compared.* For each matched drug (shared between the two reports), **start date ↔ start date** and **end date ↔ end date** are compared; for each matched adverse event, **onset date ↔ onset date**. Additionally, the **overall AE onset date** is compared at the report level (a case-level date, either explicitly reported or derived — see § Clinical date inconsistencies) and does not require shared AE terms.

Only same-field-type comparisons count. A drug's start date on one report must NOT match against the same drug's end date on the other report. Dates from unmatched drugs or unmatched AEs are not considered. Matching is **role-agnostic**. If the same substance appears more than once within a single report, every pair of same-substance entries is compared — a dateless duplicate does not shadow a dateful one. Partial dates match at their coarser resolution.

*Deduplication to unique dates.* The result is collapsed to a set of **unique matched dates**. If the same calendar date matches for multiple drugs, multiple AEs, or multiple field types, it contributes only once (at the finest matched resolution). A coarser match is also **subsumed** by a finer match whose value falls inside it: when both `2021-11-08` (day) and `2021-11` (month) appear, or `2000-04-08` (day) and `2000` (year), only the finer entry is kept. Coarser matches are retained only when they stand apart from every finer match (e.g. a year-only `2022` alongside a day `2021-11-08`). Each unique matched date contributes \(p_f = 1/D_f\).

*Domain sizing.* For matched clinical dates, domain size depends on the recorded resolution, the reporting context, and — beyond the most recent matched date — the local temporal gap to the nearest more recent matched clinical date already accounted for.

Context-dependent defaults: when the reporting context is **country-year**, a narrower 1.5-year window is used because most reports in a given country-year are temporally concentrated; for broader contexts (country or global), a 10-year window is used.

| Reporting context | Default window | Exact date \(D_f\) | Month–year \(D_f\) | Year only \(D_f\) |
| ----------------- | -------------- | ------------------- | ------------------- | ------------------ |
| Country-year      | 1.5 years      | 540                 | 18                  | 2                  |
| Country or global | 10 years       | 3650                | 122                 | 10                 |

Sequential local-gap rule: matched clinical dates contribute evidence in proportion to the *incremental* temporal extent each one establishes, not to the global temporal span of the pair. The unique matched clinical dates are sorted from most recent to oldest. The first (most recent) date is evaluated under the context-dependent default. Each subsequent date is evaluated under a domain equal to twice the temporal gap to the nearest more recent already-accounted-for matched date, expressed in the unit of that date's own resolution (days / months / years). This downweights tightly clustered additional dates while letting temporally isolated dates contribute more.

Example — matched clinical dates `2022-05-20`, `2022-05-18`, `2022-05-15`, `2019-01-01` (day resolution, country-year context), sorted most-recent-first:

| Date         | Gap to previous | Domain \(D_f\) |
| ------------ | --------------- | -------------- |
| 2022-05-20   | —               | 540 (default)  |
| 2022-05-18   | 2 days          | 4              |
| 2022-05-15   | 3 days          | 6              |
| 2019-01-01   | 1230 days       | 2460           |

Report dates and other demographic dates do not contribute to this sizing — only unique matched clinical dates do. A late report date therefore cannot inflate the domain used to score clinical-date coincidences.

Example of the date-matching itself — matched drugs cyclophosphamide, mitoxantrone, vincristine; matched AE arrhythmia:

| Entity             | Field | Report 1   | Report 2   | Match?                               |
| ------------------ | ----- | ---------- | ---------- | ------------------------------------ |
| cyclophosphamide   | start | 1994-03-30 | 1994-03-30 | **Yes** — same start date            |
| mitoxantrone       | start | 1994-03-30 | 1994-05-12 | No — different start dates           |
| mitoxantrone       | end   | 1994-05-12 | (missing)  | No — end date missing on one side    |
| vincristine        | start | 1994-03-30 | 1994-05-12 | No — different start dates           |
| vincristine        | end   | 1994-05-12 | (missing)  | No — end date missing on one side    |
| arrhythmia         | onset | 1994-05-12 | 1994-05-12 | **Yes** — same onset date            |

Unique matched dates: **{1994-03-30, 1994-05-12}** — count = 2. Note that for mitoxantrone, Report 1's end date (1994-05-12) equals Report 2's start date (1994-05-12), but this cross-field coincidence is NOT counted; 1994-05-12 enters the matched set only through the arrhythmia onset match.

### Independence classifier

The magnitude of \(L_{\text{match}}\) determines whether the matching evidence is strong enough to reject the independence null:

- \(L_{\text{match}} > \) `lmatch_limited` (default 1.0) → **Non-independent**. The pair proceeds to the relationship assessment.
- \(L_{\text{match}} \leq \) `lmatch_limited` → **Independent**, and the final label is **Unrelated** — *unless* a shared-source verdict (§ Shared-source detection) has already established non-independence.

On the \(L_{\text{match}}\) scale, values of 1, 2, and 3 correspond to multiplicity-adjusted chance-match measures of roughly 0.1, 0.01, and 0.001. The specific zone that a non-independent pair falls into (compelling / intermediate / limited) governs the *relationship* decision and is defined under § Relationship assessment (rarity-based decision zones).

## Relationship assessment

Once independence has been rejected, the relationship assessment classifies the
pair as **Same case** (`Duplicates`) or **Different case** (`Otherwise
related`), with a narrow **Abstain** branch. It uses the inconsistency signals,
interpreted in relation to the strength of the positive matching evidence, plus
source-context information from the shared-source verdict.

### Inconsistency signals

In addition to matching evidence, the method evaluates information that may indicate that two reports describe different patients. Because pharmacovigilance data often contain recording errors or incomplete information, inconsistencies do not automatically imply that reports refer to different individuals; instead they are recorded and later interpreted within the decision logic.

Each detected inconsistency is stored as a structured object describing the feature involved, the observed difference, and the severity level:

```json
{
  "feature": "height",
  "difference": 7,
  "units": "cm",
  "severity": "moderate"
}
```

`severity` is one of `minor`, `moderate`, `strong`, `decisive`. The complete set of inconsistencies for a report pair is stored on `PairFeatures.inconsistencies`, and the per-severity counts are derived when applying the decision logic.

When either report contains multiple candidate values for the same feature, the most concordant cross-report value pair is used: for numeric features, the pair with the smallest absolute difference; for categorical features, any matching value takes precedence over mismatching alternatives.

#### Severity levels

Four levels of inconsistency severity are defined:

| Severity | Interpretation                                          |
| -------- | ------------------------------------------------------- |
| minor    | small difference within typical reporting noise; mildly weakens the duplicate hypothesis |
| moderate | difference that weakens the duplicate hypothesis        |
| strong   | difference that strongly suggests different individuals |
| decisive | hard contradiction that precludes a `Duplicates` verdict |

Inconsistencies in structured data (age, sex, height, weight, country of origin, drug set) primarily span the minor, moderate, and strong severities — the exact mapping per feature is defined below. Their thresholds are based on typical variability and recording errors in pharmacovigilance data and are starting values that may need refinement. Since recording errors or misclassification are known to occur for any field on adverse event reports, strong mismatches are not considered decisive on their own and must be interpreted together with the strength of the matching evidence via the weighted inconsistency score (see § Rarity-based decision zones).

Decisive inconsistencies come from four sources: a sufficiently large age gap (≥ 11 years — see *Age* below — at which point typo / digit-transposition / unit-confusion explanations are no longer plausible), the clinical-date impossibility rule (when one report's report date predates the other report's earliest plausible overall AE onset by more than 30 days — see § Clinical date inconsistencies), the AE clinical-picture assessor's decisive verdict (no shared / compatible AEs at the case level), and the source/case override (`SAME_SOURCE_DIFFERENT_CASE`, where two reports share a source identifier but carry different comparable case labels). When present, they preclude a `Duplicates` verdict; the surrounding matching evidence then determines whether the pair is surfaced as `Otherwise related` (independence still rejected) or `Unrelated` (otherwise).

#### Features

**Age.** Absolute difference in years.

| Difference | Classification        |
| ---------- | --------------------- |
| > 0 years  | minor inconsistency   |
| ≥ 2 years  | moderate inconsistency |
| ≥ 5 years  | strong inconsistency  |
| ≥ 11 years | decisive inconsistency |

Gaps of 11 or more years cannot plausibly be explained by single-digit typos, digit transpositions, month-vs-year unit confusion, or extraction noise, so the pair is treated as decisively distinct individuals.

**Height.** Difference in centimetres.

| Difference | Classification        |
| ---------- | --------------------- |
| ≥ 1 cm     | minor inconsistency   |
| ≥ 3 cm     | moderate inconsistency |
| ≥ 6 cm     | strong inconsistency  |

Differences below 1 cm are typically rounding artefacts and are not counted.

**Weight.** Difference in kilograms.

| Difference | Classification        |
| ---------- | --------------------- |
| ≥ 1 kg     | minor inconsistency   |
| ≥ 3 kg     | moderate inconsistency |
| ≥ 10 kg    | strong inconsistency  |

Differences below 1 kg are typically rounding artefacts and are not counted.

**Sex.** A mismatch in recorded patient sex is a **strong** inconsistency (`sex_R1 ≠ sex_R2`). Missing values on either side produce no inconsistency.

**Country of origin.** A mismatch (no shared country value) is a **minor** inconsistency. Cross-country duplication does occur (a case originally reported in one country may resurface in another), so the mismatch is given limited weight — enough to keep reviewers aware without materially suppressing duplicate candidates that differ only by reporting country. Missing values on either side produce no inconsistency, and any shared country value removes it.

**Drug set.** Drug-set inconsistencies detect when a clinically important drug from one report — or one of its ingredient components — is absent from the other report's drug list. Drugs are classified by their **role**: `suspected` and `interacting` are **key drugs**; `concomitant` and role-missing/unknown are non-key (background medication).

The check is **directional**: for each report, its key drugs are compared against the full drug list (all roles) of the other report. Each direction produces at most one moderate inconsistency, regardless of how many key drugs are missing. Presence is evaluated at the level of **ingredient components** rather than whole substance strings.

```
For each direction (r1→r2, r2→r1):
  key_drugs(source)     = substances with role ∈ {suspected, interacting}
  components(name)      = name split on ';', each part stripped + lowercased
  target_components     = ⋃ components(s) over all substances s of target (any role)
  missing = { key ∈ key_drugs(source)
              : components(key) ⊄ target_components }   # some component absent
  if missing is non-empty → 1 moderate inconsistency
```

This yields 0, 1, or 2 moderate inconsistencies per pair.

*Combination-product override.* In WHODrug, ';' separates the ingredients of a combination product. A product coded as the combination on one report (`valsartan;amlodipine`) and as separate ingredients on the other (`valsartan` + `amlodipine`) is a coding-convention difference, not a clinical discrepancy, so it must not be penalised. Because presence is checked per component against the union of the target's components, such a pair produces **no** inconsistency in either direction. A genuinely unreported ingredient still fires — a key `valsartan;amlodipine` against a report listing only `valsartan` leaves `amlodipine` missing, so that direction is inconsistent. Matching remains **exact at the component level, never substring**, so distinct substances whose names contain one another (`prednisolone` vs `methylprednisolone`) still mismatch. This override affects only the inconsistency check; the matched drug set feeding \(L_{\text{match}}\) is unchanged and still keyed on whole substance strings.

Design notes: drug name matching is case-insensitive (component whitespace stripped); a drug present in both reports with different roles is not inconsistent; reports with no key drugs produce no drug-set inconsistencies; only drug **presence** is evaluated — dosage, form, and date differences are not (date differences are handled by § Clinical date inconsistencies).

#### Clinical date inconsistencies

Clinical-date inconsistencies are detected by a dedicated rule-based module (`feature_generation/clinical_date_inconsistencies.py`), producing fine-grained, severity-bucketed counts.

*Compared entities:*

- **Overall AE onset date** (one per report — highest priority; an explicitly reported case-level date or, when absent, a derived value — see below)
- **Drug start dates** (matched by normalized substance name)
- **AE onset dates** (matched by normalized PT name)
- **Drug end dates** — downgrade-only evidence, never primary inconsistency sources

The module exposes bucketed counts (`n_minor` / `n_moderate` / `n_strong` / `n_decisive`) on `clinical_date_inconsistency_counts` and individual selected findings on `clinical_date_findings`.

*Overall AE onset date.* A single canonical "effective" overall AE onset date per report is resolved as: (1) if `Report.reported_overall_ae_onset_date` is present (e.g. extracted from a `CASE EVENT DATE: YYYYMMDD` narrative pattern), it is used as-is; (2) otherwise, the fallback derivation below produces a value. This is encapsulated in `effective_overall_ae_onset_date(report)`.

The fallback derivation aims to pick the earliest AE onset date and only refrains when there is *clear evidence* an early onset is incompatible with the suspected drugs, erring on the side of keeping early AE onsets. For each report: (1) no AE onset dates → missing; (2) no suspected-drug start dates → the earliest AE onset (unconditional fallback); (3) otherwise, group suspected-drug start dates by normalized substance name and pick the earliest AE onset **compatible** with every suspected substance. An AE onset `ae` is compatible with a substance when *any* of that substance's regimen start dates `r` satisfies `earliest_plausible(r) ≤ latest_plausible(ae)`. Grouping by substance ensures a re-challenge keeps the AE in the candidate set via the earlier regimen. Consequences: a month-resolution start `1968-11` does **not** disqualify a day-resolution onset `1968-11-12`; a day-resolution start `2020-02-01` does disqualify an onset `2020-01-15`. If no AE onset is compatible with every suspected substance, the derivation returns missing.

*Counterpart selection — alignment across repeated entries.* The same normalized drug substance may appear with multiple distinct start dates (re-challenge regimens, different dosing periods), and the same PT with several onset dates. Comparing all cross-pairs would produce spurious "strong" findings whenever the reports disagree on which regimen is primary. Instead, for each matched entity: (1) group by normalized entity; (2) deduplicate exact-duplicate date values within a report (a non-concomitant role dominates a concomitant one for that slot); (3) **select the single best-matching date pair** — the pair with the smallest interval-aware minimum possible day difference, ties broken deterministically; (4) emit at most one candidate finding, from that best pair — every other date is a leftover and does not generate a finding. An entity is therefore flagged only when even its best-matching pair is inconsistent.

Drug **role is ignored** when choosing the counterpart (selection minimises date distance only); the source-side role is retained so the concomitant-drug downgrade still applies per direction. A deliberate consequence is that a genuine second-regimen mismatch is **masked** whenever a first date matches well (a shared start date alongside a far-apart second date yields no finding) — intentional, since the per-entity date signal is already down-weighted, and it avoids force-pairing two unrelated leftover dates once an exact match is consumed. Cross-direction deduplication uses a pair-aware key `(entity_label, {source_date, matched_date})`, collapsing the two directions of the selected pair into one finding.

*Severity thresholds.* All clinical-date day-difference comparisons grade against a single canonical table — the *overall AE onset vs. overall AE onset* comparison applies it directly; per-entity comparisons apply the same table and then downgrade one step. A separate decisive-impossibility check on the report date vs. overall AE onset is layered on top.

Overall AE onset date vs. overall AE onset date (canonical table, applied directly):

| Difference | Severity |
| ---------- | -------- |
| 0 days | none |
| 1–7 days | minor |
| 8–75 days | moderate |
| > 75 days | strong |

Per-entity drug start / AE onset dates (canonical table, then downgrade one step — *minor → none*, *moderate → minor*, *strong → moderate*; this caps a single regimen- or PT-level disagreement at moderate, since case-level temporal disagreement is captured by the overall-AE-onset comparison):

| Difference | Severity |
| ---------- | -------- |
| 0–7 days | none |
| 8–75 days | minor |
| > 75 days | moderate |

Report date vs. overall AE onset date (decisive impossibility):

| Difference (report date precedes earliest plausible overall AE onset by…) | Severity |
| ---------- | -------- |
| ≤ 30 days | none (grace) |
| > 30 days | decisive |

This applies even with incomplete dates — e.g. report date `1999-02-10` vs onset year `2005` is decisive. The 30-day grace window addresses an observed reporting pattern where paired reports each list the day of reporting as the AE onset (data-entry imprecision) rather than a genuine timeline conflict; without it, otherwise highly consistent pairs arriving a few days apart were being vetoed out of `Duplicates`. Smaller disagreements between the two reports' overall AE onset dates are still surfaced by the overall-vs-overall comparison above, so the grace window silences only the binary impossibility veto.

*Incomplete dates.* Partial dates are converted to intervals (`[earliest, latest]` plausible date). The *minimum possible* day difference between two intervals is used. A severity is only assigned when the category is guaranteed even under the most generous interpretation — avoiding false positives at the cost of occasionally missing a discrepancy when resolution is too coarse.

*Downgrade rules (applied cumulatively):*

1. *Cross-entity downgrade*: if the source date exactly matches any clinically relevant date (drug start, AE onset, overall AE onset, or drug end date) on the other report, the severity is reduced by one step (strong → moderate, moderate → minor, minor → ignored). A minor discrepancy whose source date coincides with another date on the opposite report is the most likely to be a field-labeling artefact, so it drops out entirely.
2. *Concomitant drug downgrade*: for concomitant-drug start date mismatches, severity is reduced by one step (moderate → minor, minor → ignored). Because per-entity drug start severity is capped at moderate, a two-step downgrade would always wipe concomitant findings out entirely; a single-step downgrade keeps concomitant moderates contributing as minor.

*Deduplication.* Within direction (one inconsistency per source date): each unique source date on a report contributes at most one counted inconsistency, with priority overall AE onset > suspected-drug start > AE onset > concomitant-drug start; if the highest-priority entity has no inconsistency, a lower-priority entity may contribute. Across directions: the comparison is evaluated both ways, and a cross-direction step ensures each entity produces at most one counted inconsistency, keeping the lower final severity (honouring direction-specific downgrades conservatively).

#### AE clinical-picture inconsistency

A dedicated component evaluates whether the two reports describe a clinically inconsistent **adverse-event picture** — independently of chronology, which is handled by the rule-based clinical-date inconsistency module. Two reports can share dates yet describe clinically different AE pictures (and conversely differ in wording while describing the same picture); rule-based set comparison alone cannot distinguish these.

Two interchangeable assessors produce the same `AEClinicalPictureResult` shape:

* **LLM-assisted** — the original component; uses an LLM to read AE lists plus narratives and emit clinically grouped findings.
* **Rule-based / deterministic** — a non-LLM fallback using the primary MedDRA hierarchy alone (PT / primary HLT / primary HLGT / primary SOC).

`PipelineRunner.ae_clinical_picture_mode` (`"llm"` | `"rule_based"` | `"auto"`, default `"auto"`) selects between them; `"auto"` uses the LLM when a client is configured and falls back to the rule-based assessor otherwise (and on LLM exception). `ae_clinical_picture_method` on `PairFeatures` records which path produced the result.

*LLM-assisted assessor — output shape.* The LLM returns a list of distinct, clinically non-redundant findings rather than a single global label. Each finding carries `kind` ∈ {`unmatched_cluster`, `matched_characterization`, `global_picture_mismatch`}, `severity` ∈ {`minor`, `moderate`, `strong`, `decisive`}, and a short `rationale`, involved AE `terms`, and optional human `cluster_label`. A deterministic dedup step suppresses all other findings from the severity counts when any finding has `kind = global_picture_mismatch` and `severity = decisive` (suppressed findings remain in the master list with `suppressed = true`).

*LLM-assisted assessor — severity rubric.*

| Severity | Interpretation |
| --- | --- |
| minor | small, peripheral, or compatible-but-broader difference |
| moderate | clinically meaningful non-redundant difference, not dominant |
| strong | substantial difference materially weakening same-case interpretation |
| decisive | no shared / compatible AEs; clearly different pictures at the case level |

The model is instructed to anti-overcount: group related unmatched AEs into a single finding, tolerate a few unmatched terms in long lists with broad overlap, treat one-sided subset-style differences leniently, and avoid stacking subordinate findings under a decisive global mismatch.

*Rule-based assessor — algorithm.* Each `AdverseEvent` carries the primary MedDRA hierarchy (`primary_hlt_name`, `primary_hlgt_name`, `primary_soc_name`); all hierarchy-name comparisons are case-folded, and empty/missing values never match.

**Step 1 — global clinical picture overlap (symmetric, includes Investigations PTs).** Find the most granular shared level between any reported PT in report 1 and any in report 2:

| Most granular overlap | Severity |
| --- | --- |
| exact PT match | none |
| shared primary HLT | minor |
| shared primary HLGT | moderate |
| shared primary SOC | strong |
| no shared primary SOC | decisive |

Step 1 deliberately uses *all* reported PTs, including Investigations-SOC PTs, and looks for any shared clinical core.

**Step 2 — directional unmatched clinical content (both `R1 → R2` and `R2 → R1`, excludes Investigations PTs).** For source A and target B, filter out PTs whose primary SOC is Investigations on both sides. Then for each remaining source PT: exact PT match in target → `M`; else source HLT present in target's HLT set → `M` (clinically compatible coding difference); else source HLT non-empty and absent from target → `U` (unmatched); else (source HLT missing) → `M` (fail-safe — cannot claim "unmatched" without hierarchy info).

| Condition | Severity |
| --- | --- |
| `U == 0` | none |
| `U == 1` | minor |
| `U / (U + M) < 0.5` | minor |
| otherwise | moderate |

Step 2 counts **unmatched PT burden**, not clinically distinct clusters, so PTs that differ at PT level but share an HLT do not count as unmatched; bidirectional unmatched burden naturally produces a larger penalty than a simple subset relation. Investigations-SOC PTs are excluded from both `U` and `M`, but Step 1 still considers them so a shared lab-finding overlap is not lost. The full Step-1 / Step-2 audit is persisted on `PairFeatures.ae_clinical_picture_rule_based_details`.

*Assessment status.* Findings are accompanied by a status distinguishing "did look" from "did not look":

| `ae_clinical_picture_status` | `ae_clinical_picture_reason` | `ae_clinical_picture_method` | When |
| --- | --- | --- | --- |
| `assessed` | `""` | `llm` | LLM was called and produced a verdict |
| `assessed` | `rule_based` | `rule_based` | Deterministic assessor ran (`mode="rule_based"`, or `mode="auto"` with no LLM client and hierarchy present) |
| `assessed` | `llm_fallback_to_rule` | `rule_based` | `mode="auto"`: LLM raised → fell back to deterministic assessor |
| `not_assessed` | `no_llm_client` | `llm` | `mode="llm"` and no LLM client configured |
| `not_assessed` | `trivially_matched` | `llm` | normalised AE PT sets equal AND both narratives empty |
| `not_assessed` | `llm_error` | `llm` | `mode="llm"`: LLM call raised an exception |
| `not_assessed` | `missing_hierarchy` | `rule_based` | rule-based path, but no PT carries MedDRA hierarchy info |

Downstream code must treat `not_assessed` as *no information*, not as "assessed and clean".

*Integration.* Findings from either assessor are appended to the unified `PairFeatures.inconsistencies` list (`source` = `"llm_ae_clinical_picture"` vs. `"rule_ae_clinical_picture"`). Severity counts (excluding suppressed LLM entries) are merged into `inconsistency_counts` alongside the rule-based and clinical-date counts. The final classification remains rule-based and probability-anchored — this assessment feeds the adjudication via `inconsistency_counts`, never replacing it.

### Weighted inconsistency score

The per-severity counts are combined into a single weighted score:

$$
w_{\text{tot}} = w_{mi} \times n_{\text{minor}} + w_m \times n_{\text{moderate}} + w_s \times n_{\text{strong}} + w_d \times n_{\text{decisive}}
$$

The counts include all sources: demographic inconsistencies (age, height, weight, sex), country-of-origin inconsistencies, drug-set inconsistencies, clinical-date inconsistencies, AE clinical-picture inconsistencies, and the source/case override decisive inconsistency (when `SAME_SOURCE_DIFFERENT_CASE`). The symbols \(w_{mi}\), \(w_m\), \(w_s\), \(w_d\) correspond to `w_minor`, `w_moderate`, `w_strong`, `w_decisive` (see § Parameter tuning). With the current defaults (\(w_{mi} = 0.4\), \(w_m = 1\), \(w_s = 3\), \(w_d = 100\)) a single strong inconsistency contributes 7.5 times as much as a minor one, and the decisive weight is deliberately oversized so any decisive count always exceeds every zone tolerance.

### Source-context evidence zones

When a shared-source verdict is present, source-context evidence takes priority over the ordinary rarity-based zone logic, because the informativeness of drugs, adverse events, and inconsistencies differs between single-patient and multi-patient materials. Per-verdict sub-label rules (the verdict is computed in § Shared-source detection):

| Verdict | Sub-label mapping |
|---|---|
| `SAME_SOURCE_SINGLE_PATIENT` | `Duplicates` if \(w_{\text{tot}} <\) `w_tot_compelling_zone`; else `Abstain` |
| `SAME_SOURCE_SAME_CASE`      | `Duplicates` if \(w_{\text{tot}} <\) `w_tot_compelling_zone`; else `Abstain` |
| `SAME_SOURCE_DIFFERENT_CASE` | `Otherwise related` (pinned) |
| `SAME_SOURCE_UNCOMPARABLE_CASE` | two-way rule — see below |
| `MULTI_PATIENT_CONTEXT_NO_LINK` | same two-way rule when \(L_{\text{match}} >\) `lmatch_limited`; ordinary zone logic (→ `Independent`) otherwise |

#### SAME_SOURCE_SINGLE_PATIENT and SAME_SOURCE_SAME_CASE

These verdicts default to `Duplicates`. When non-source-case inconsistency signals push \(w_{\text{tot}}\) to or past `w_tot_compelling_zone` (default `4.0`), the pair is **Abstained** rather than classified as `Otherwise related`. The rationale: for a genuinely single-patient source (or one where the same case identifier is confirmed), substantial inconsistencies are more plausibly caused by extraction artefacts, secondary-source corruption, reporting differences, or partial information than by the reports describing distinct individuals. The pair is therefore epistemically underdetermined, and an explicit `Abstain` is preferable to a false `Otherwise related` classification.

The threshold is shared with the compelling L_match zone by design: a confirmed shared identifier coupled with a single-patient / same-case claim is at least as strong evidence of identity as compelling rarity-based matching. A single strong inconsistency is tolerated; reaching the threshold requires one strong + one moderate, two strongs, four moderates, or equivalent. Decisive inconsistencies trigger the threshold via their large weight (`w_decisive = 100`).

#### SAME_SOURCE_DIFFERENT_CASE

Pinned to `Otherwise related` regardless of \(w_{\text{tot}}\). Two reports sharing a DOI but carrying different comparable case labels clearly identify different individuals within the same multi-patient source. `feature_generation/source_case_inconsistency.py` synthesizes a **decisive** `Inconsistency` row (informational, audit-facing; the pin is what enforces the outcome).

#### SAME_SOURCE_UNCOMPARABLE_CASE

The shared source is confirmed, but case identifiers are missing or not safely comparable. UNCOMPARABLE_CASE specifically covers literature and summary-case publications, where reported demographics are deliberate and a discrepancy is more likely to reflect a different patient than an extraction artefact. A two-way rule is applied:

| Branch | Condition | Outcome |
|---|---|---|
| B | \(w_{\text{tot}} \geq\) `w_tot_limited_zone` | `Otherwise related` |
| A | \(L_{\text{patient}} > \) `lpatient_compelling` (with \(w_{\text{tot}} <\) `w_tot_limited_zone` implied) | `Duplicates` |
| C | otherwise | `Otherwise related` (default) |

Branch B is checked first so any inconsistency above the limited-zone bar reaches `Otherwise related` regardless of patient-specific positive evidence. Branch A promotes to `Duplicates` only when the patient-specific evidence is compelling; every remaining case falls through branch C to `Otherwise related`. The rule therefore never abstains — non-independence is established, so a pair that is not convincingly the same individual is simply reported as `Otherwise related` rather than left undecided.

Note the lower Otherwise related gate here (`w_tot_limited_zone`, default 1.0) compared to the SINGLE_PATIENT / SAME_CASE Abstain gate (`w_tot_compelling_zone`, default 4.0): SINGLE_PATIENT / SAME_CASE has a confirmed single-patient identity claim, so inconsistencies are more plausibly artefacts; UNCOMPARABLE_CASE has no such claim, so small inconsistencies in a curated publication carry more weight as evidence of different patients. Calibrated behavior:

| Age difference | Severity | \(w_{\text{tot}}\) | Outcome |
|---|---|---|---|
| 0 (exact match) | none | 0.0 | (driven by branch A — Duplicates if L\_patient clears, else Otherwise related) |
| 1 year | minor | 0.4 | Otherwise related or Duplicates (depending on branch A) |
| 3 years | moderate | 1.0 | **Otherwise related** |
| 5 years | strong | 3.0 | Otherwise related |

Note: `SAME_SOURCE_UNCOMPARABLE_CASE` emits **no** `Inconsistency` (it represents *absence of comparable evidence*, not a discrepancy), so \(w_{\text{tot}}\) reflects only non-source-case signals and the comparison is apples-to-apples.

##### Patient-specific log-evidence \(L_{\text{patient}}\)

\(L_{\text{patient}}\) is the sum of \(-\log_{10}(p_f)\) over the per-feature contributions in `PairFeatures.probability.feature_contributions`, restricted to the patient-specific features and carrying **no multiplicity adjustment**:

$$
L_{\text{patient}} = \sum_{f \in F_{\text{patient}}} -\log_{10}(p_f)
$$

where \(F_{\text{patient}} = \{\texttt{patient\_sex}, \texttt{patient\_age}, \texttt{patient\_height}, \texttt{patient\_weight}, \texttt{clinical\_date}\}\).

Equivalently, \(L_{\text{patient}} = -\log_{10}\!\left(\prod_{f \in F_{\text{patient}}} p_f\right)\): the chance-match probability for this one pair restricted to the patient-specific features, on the \(\log_{10}\) scale.

Two deliberate departures from the standard \(L_{\text{match}}\):

- **Drug-set and AE-set contributions excluded.** In a multi-patient publication / shared study context, drugs and adverse events overlap by construction across patients and are therefore weak identifiers of patient identity. This is enforced with an explicit allowlist (`PATIENT_SPECIFIC_FEATURES = {"patient_age", "patient_sex", "patient_height", "patient_weight", "clinical_date"}` in `feature_generation/probability.py`), so only patient-identifying features contribute and any other feature name is dropped.
- **No anchor multiplicity adjustment.** \(L_{\text{match}}\) asks how probable the observed match would be to arise by chance *somewhere in the anchor population*, adjusted for the \(M = \binom{N_{\text{anchor}}}{2}\) pairwise opportunities. For `UNCOMPARABLE_CASE` the relevant comparison universe is instead the unknown small set of K patients within the shared publication, so adjusting for the anchor would correct for opportunities not actually taken. \(L_{\text{patient}}\) therefore stays at the per-pair level.

This is **not** \(L_{\text{match}}\) minus a penalty term. Since \(L_{\text{match}} = \max(0, -\log_{10}(P_{\text{match}} + \varepsilon))\) with \(P_{\text{match}} = 1-(1-p_{\text{joint}})^{M}\), it does not decompose additively; the two coincide only in the rare-event limit. \(L_{\text{patient}}\) is an evidence measure computed during feature generation (`compute_l_patient`, carried on `PairFeatures.L_patient`), a pure function of the per-feature contributions, judged against its own bar `lpatient_compelling`. It is computed for every pair but consumed only on this branch.

The branch A bar `lpatient_compelling` (default 2.0) is set so that intuitively informative concordances qualify:

| Matched features | \(-\log_{10}(p_f)\) | Sum | Clears 2.0? |
|---|---|---|---|
| sex only | 0.30 | 0.30 | no |
| age only (1/80) | 1.90 | 1.90 | no |
| sex + age | 0.30 + 1.90 | 2.20 | **yes** |
| age + weight (1/40) | 1.90 + 1.60 | 3.50 | yes |
| sex + age + clinical date (drug start, 1/540) | 0.30 + 1.90 + 2.73 | 4.93 | yes |

`lpatient_compelling` is a **separate threshold from the \(L_{\text{match}}\) zone boundaries**, even though its default (2.0) coincides with `lmatch_intermediate`. The two bars apply to different quantities on different scales and must move independently: retuning an \(L_{\text{match}}\) zone must not silently shift the patient-identity bar.

The branch A inconsistency gate is `w_tot < w_tot_limited_zone` (default 1.0). Because branch B is checked first on the complementary condition, the two partition the \(w_{\text{tot}}\) axis at the same bar: any moderate-or-stronger inconsistency — or three accumulated minors (\(3 \times 0.4 = 1.2\)) — lands the pair in `Otherwise related`, while a single minor (0.4) is tolerated. Below the bar the outcome rests entirely on \(L_{\text{patient}}\): compelling patient-specific evidence yields `Duplicates`, and everything else falls through branch C to `Otherwise related`.

#### MULTI_PATIENT_CONTEXT_NO_LINK

This verdict fires when at least one narrative indicates a multi-patient source (literature series, shared-study cohort, drug-shortage cluster, …), neither narrative explicitly indicates a single-patient source, and no reliable shared source identifier links the two reports. Accepting a one-sided multi-patient indication (rather than requiring both) keeps the signal robust to reports that lack narratives or literature-reference fields, and mirrors how a one-sided `single_patient_source` suffices for `SAME_SOURCE_SINGLE_PATIENT`; an explicit single-patient claim on either side vetoes the verdict. The drug-set / AE-set inflation concern that motivates the UNCOMPARABLE_CASE two-way rule applies here as well.

The dispatch is gated on the matching evidence:

- **\(L_{\text{match}} > \) `lmatch_limited`** — route through the same two-way rule as `SAME_SOURCE_UNCOMPARABLE_CASE`, using \(L_{\text{patient}}\) for branch A and `w_tot_limited_zone` for the branch B / branch A inconsistency gate. So a pair with high \(L_{\text{match}}\) and a single moderate inconsistency lands in `Otherwise related` rather than being absorbed into `Duplicates` by the compelling zone's looser tolerance.
- **\(L_{\text{match}} \leq \) `lmatch_limited`** — fall through to the ordinary weak-threshold branch (→ `Independent`). Without positive shared-source evidence we do not promote a weak-matching pair into the override territory.

Conceptually this asymmetry mirrors the underlying evidence: `UNCOMPARABLE_CASE` has a confirmed shared source identifier and always invokes the two-way rule; `MULTI_PATIENT_CONTEXT_NO_LINK` lacks that anchor and therefore requires enough matching evidence to make the override worth applying. The chosen branch is recorded as `independence_reason = shared_source` in either case.

### Rarity-based decision zones

Absent a shared-source verdict, a non-independent pair's sub-label is decided by comparing \(w_{\text{tot}}\) against a tolerance that depends on which \(L_{\text{match}}\) zone the pair falls in. Each zone applies a progressively stricter tolerance:

| Zone | Reason code | Condition | Duplicates if... | Otherwise |
|---|---|---|---|---|
| Compelling evidence | `rarity_compelling` | \(L_{\text{match}} > 3.0\) | \(w_{\text{tot}} < 4\) | Non-independent / Otherwise related |
| Intermediate evidence | `rarity_intermediate` | \(L_{\text{match}} > 2.0\) | \(w_{\text{tot}} < 3\) | Non-independent / Otherwise related |
| Limited evidence | `rarity_limited` | \(L_{\text{match}} > 1.0\) | \(w_{\text{tot}} < 1\) | Non-independent / Otherwise related |
| Below threshold | `rarity_insufficient` | \(L_{\text{match}} \leq 1.0\) | never | Independent (Unrelated) |

In all three non-trivial zones the matching evidence is treated as strong enough to reject the independence null on its own, so the pair is `Non-independent`; the inconsistency score then decides `Duplicates` vs `Otherwise related`. The zones differ only in how much inconsistency is tolerated before the verdict is downgraded from `Duplicates`. Below the weak threshold the matching evidence is too weak to reject independence regardless of how clean the structured comparison looks. The L\_match zone boundaries (3.0, 2.0, 1.0), the zone-specific \(w_{\text{tot}}\) thresholds (4, 3, 1), and the inconsistency weights are all configurable.

**Examples** (default parameters):

- \(L_{\text{match}} = 4.2\), one strong inconsistency: \(w_{\text{tot}} = 3 < 4\) → **Non-independent / Duplicates**
- \(L_{\text{match}} = 4.2\), one strong + one moderate: \(w_{\text{tot}} = 4\), not \(< 4\) → **Non-independent / Otherwise related**
- \(L_{\text{match}} = 2.5\), two moderate inconsistencies: \(w_{\text{tot}} = 2 < 3\) → **Non-independent / Duplicates**
- \(L_{\text{match}} = 2.5\), one strong inconsistency: \(w_{\text{tot}} = 3\), not \(< 3\) → **Non-independent / Otherwise related**
- \(L_{\text{match}} = 1.5\), no inconsistencies: \(w_{\text{tot}} = 0\) → **Non-independent / Duplicates**
- \(L_{\text{match}} = 1.5\), one moderate inconsistency: \(w_{\text{tot}} = 1\), not \(< 1\) → **Non-independent / Otherwise related**
- \(L_{\text{match}} = 0.8\): below threshold → **Independent (Unrelated)** regardless of inconsistencies

### Decisive inconsistencies

Decisive inconsistencies (clinical-date impossibility, an AE clinical-picture decisive verdict, or `SAME_SOURCE_DIFFERENT_CASE`) participate in the zone-based logic as a term in \(w_{\text{tot}}\) with a deliberately large weight \(w_d = 100\), high enough that any non-zero decisive count drives \(w_{\text{tot}}\) past every zone tolerance. A configuration validator enforces \(w_d > \max(\text{zone thresholds})\). The effect is the same as an explicit override but expressed through the ordinary weighted score, keeping the breakdown transparent. When a decisive inconsistency is present:

- \(L_{\text{match}} > 1.0\) → **Non-independent / Otherwise related** (matching evidence still rejects independence, so the pair is surfaced as related but not the same case)
- \(L_{\text{match}} \leq 1.0\) → **Independent (Unrelated)** (independence cannot be rejected, so no claim about relatedness is made)

In other words, decisive inconsistencies preclude `Duplicates` but do not by themselves overturn the independence assessment.

### Decision flow summary

Putting the two assessments together, the decision logic is gated on whether the source/case assessment fires the shared-source override:

1. **If the override fires** — verdict is one of `SAME_SOURCE_SINGLE_PATIENT`, `SAME_SOURCE_SAME_CASE`, `SAME_SOURCE_DIFFERENT_CASE`, `SAME_SOURCE_UNCOMPARABLE_CASE`, or — gated on \(L_{\text{match}} >\) `lmatch_limited` — `MULTI_PATIENT_CONTEXT_NO_LINK`. The verdict-specific rule under § Source-context evidence zones determines the outcome.
2. **Otherwise** — including the advisory verdicts `NO_DECISIVE_SIGNAL` and `NOT_ASSESSED`, and `MULTI_PATIENT_CONTEXT_NO_LINK` when \(L_{\text{match}} \leq\) `lmatch_limited` — the rarity-based zone logic under § Rarity-based decision zones applies, with decisive inconsistencies handled as above.

The chosen branch is recorded on `ClassificationResult.independence_reason` (`shared_source` for the override path; one of `rarity_compelling` / `rarity_intermediate` / `rarity_limited` / `rarity_insufficient` for the zone paths).

## Parameter tuning

The following parameters define the decision behaviour and are configurable:

| Parameter | Default | Role |
|---|---|---|
| `T` (anchor threshold) | 50 | Select marginal vs joint clinical anchor (§ Anchor selection) |
| `lmatch_compelling` | 3.0 | L\_match boundary for the compelling evidence zone |
| `lmatch_intermediate` | 2.0 | L\_match boundary for the intermediate evidence zone |
| `lmatch_limited` | 1.0 | L\_match boundary for the limited evidence zone (and the independence threshold) |
| `lpatient_compelling` | 2.0 | L\_patient bar for `Duplicates` in the shared-source / uncomparable-case two-way rule; separate from the L\_match zones |
| `w_minor` | 0.4 | Weight per minor inconsistency in \(w_{\text{tot}}\) |
| `w_moderate` | 1.0 | Weight per moderate inconsistency in \(w_{\text{tot}}\) |
| `w_strong` | 3.0 | Weight per strong inconsistency in \(w_{\text{tot}}\) |
| `w_decisive` | 100 | Weight per decisive inconsistency; validator requires it to exceed every zone threshold |
| `w_tot_compelling_zone` | 4.0 | Strict upper bound on \(w_{\text{tot}}\) for `Duplicates` in the compelling zone |
| `w_tot_intermediate_zone` | 3.0 | Strict upper bound on \(w_{\text{tot}}\) for `Duplicates` in the intermediate zone |
| `w_tot_limited_zone` | 1.0 | Strict upper bound on \(w_{\text{tot}}\) for `Duplicates` in the limited zone |

These parameters may be refined during method development using annotated report pairs.

### Calibrating them

Because \(L_{\text{match}}\), \(L_{\text{patient}}\) and the inconsistency counts are all computed during feature generation, and `classify` is a pure function of `(PairFeatures, DecisionThresholds)`, the decision can be replayed under different thresholds without re-running the pipeline. Persisting `PairFeatures` (`pipeline.export.write_pair_features`) makes the refinement executable offline — no reports, no database, no language model — against annotated pairs (see `eval.sweep`).

Three properties of the scoring must be respected when reading a sweep, each of which can otherwise produce a confidently wrong answer:

- **Abstained pairs leave the denominator.** Under `AbstainMode.DECIDED_ONLY` a pair the pipeline abstains on is excluded from \(n\) entirely, so a threshold setting that abstains on the pairs it would have misclassified scores *better* on fewer pairs. F1 is therefore not comparable across settings whose \(n\) differs, and no threshold should be selected by maximising it.
- **Pair orientation is not canonical.** `pair_id` is `{report_id1}__{report_id2}` and nothing sorts the two, so an annotation file listing a pair in the opposite order matches nothing. Check the coverage report before reading any metric.
- **Only ratios matter among the weights.** `w_minor` … `w_strong` and the `w_tot_*_zone` thresholds are not independent; sweeping both jointly explores a redundant space. Vary one side. `w_decisive` is not a free parameter — the validator requires it to exceed every zone threshold.
