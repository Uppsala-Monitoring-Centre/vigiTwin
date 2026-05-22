# Duplicate Adjudication Method Specification

This document specifies the method used by the **duplicate adjudication
pipeline** to classify candidate report pairs that have already been flagged
as suspected duplicates by an upstream candidate-generation method (e.g.
vigiMatch). For each pair the pipeline produces a final classification of
**Duplicates**, **Otherwise related**, or **Unrelated**.

The method is primarily a transparent rule-based decision logic operating on
structured features, with optional LLM-assisted narrative analysis. It draws
on three complementary kinds of evidence:

1. **Rarity-based matching evidence**, which quantifies how unlikely the
   observed combination of matched features would be by chance within a
   relevant comparison population (the *anchor population*).
2. **Inconsistency signals**, which capture structured or narrative
   information that argues against the duplicate hypothesis.
3. **Narrative consistency** (planned), an LLM assessment of whether the
   free-text narratives are clinically compatible.

The probabilistic component is framed as a test of the **independence
null hypothesis**: it estimates how surprising the matched features would
be if the two reports were independent draws from the comparison
population. A small expected number of collisions is evidence *against*
independence, but it is not specifically evidence that the two reports
describe the same individual: same reporter, same study, copies of the
same case routed through different channels, or unusual shared context
can all produce a small expected-collision count without the reports
describing the same patient. The pipeline therefore distinguishes two
internal layers (see § Decision logic):

* an **independence assessment** (`Non-independent / Independent`)
  driven by the rarity-based matching evidence, and
* when independence has been rejected, a **duplicate relationship**
  sub-label (`Duplicates / Otherwise related`) driven by the
  inconsistency signals.

The user-facing output remains `Duplicates / Otherwise related /
Unrelated` and is produced by combining the two internal layers (see
`ClassificationResult.final_label`).

## Evidence components

### Probabilistic matching framework

The probabilistic framework evaluates how surprising the observed combination of matched features would be if the two reports originated from different individuals within the anchor population.

Intuitively, if many reports exist within the anchor population that share similar drugs and adverse events, then additional matches (such as clinical dates or patient characteristics) may occur by chance. Conversely, if the probability of observing the full set of matched features is extremely small, the reports are unlikely to describe different individuals.

#### Reporting Context and Anchor Population

The evidential value of matching report attributes depends on the population within which random matches may occur. 

This population is defined by the **reporting context** and the **anchor population**.

---

##### Reporting Context

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

---

##### Matching Clinical Feature Sets

For each report pair, the following sets of shared clinical features are determined:

```
matching_drugs
matching_AEs
```

Each set may contain one or more elements.

Example:

```
matching_drugs = {sertraline, rosuvastatin}
matching_AEs = {rash, pruritus}
```



---

##### Candidate Anchor Populations

Within the reporting context, three candidate populations are computed:

```
N_drugs = number of reports containing all matching drugs
N_AEs = number of reports containing all matching adverse events
N_drugs+AEs = number of reports containing both
```

These populations represent progressively narrower clinical definitions.

---

##### Anchor Selection

The anchor population defines the baseline population within which collision probabilities are evaluated.

Let \(T\) denote a configurable threshold (e.g. \(T = 50\)).

The anchor is selected as follows:

0. If either \(N_{\text{drugs}}\) or \(N_{\text{AEs}}\) is zero (no overlap for that set), that candidate is skipped and the other is used. If both are zero the anchor is zero (degenerate case).

1. If either \(N_{\text{drugs}}\) or \(N_{\text{AEs}}\) is less than or equal to \(T\), select the smaller of the two.

2. Otherwise, use the intersection:

$$
\text{anchor} = \text{drugs} \cap \text{AEs}
$$

The size of the selected population is:

$$
N_{\text{anchor}}
$$

---

##### Interpretation

The anchor population defines the group of reports within which the observed matches could plausibly occur by chance.

A small anchor population indicates that the shared clinical context is rare, meaning that even modest additional matches may provide strong evidence that the reports describe the same patient.

Conversely, large anchor populations imply that many similar reports exist, requiring stronger supporting evidence to establish duplication.



#### Probability estimation

After identifying matching features between the two reports, the evidential strength of the match is evaluated by estimating how likely the **combined pattern of matched features** would be to occur by chance within the anchor population.

The features that contribute to the composite matching probability are:

- **Drug set** — when not part of the anchor definition (leave-pair-out marginal at the narrowest scope with extra-pair evidence; primary → country → global → floor)
- **AE set** — when not part of the anchor definition (leave-pair-out marginal at the narrowest scope with extra-pair evidence; primary → country → global → floor)
- **Patient demographics** — age, sex, height, weight (domain-size based)
- **Clinical dates** — unique dates matching across drug start/end, AE onset, and overall AE onset fields (domain-size based)

Note: **report date is excluded** from the probability computation. It is matched for evidence display purposes only, as it primarily reflects administrative timing rather than patient-identifying clinical information.

The framework proceeds in three steps:

1. Determine a feature-level probability \(p_f\) for each matched feature.
2. Combine these probabilities across features.
3. Estimate the expected number of such combined matches within the anchor population.

---

##### Feature-Level Probability

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

---

##### Composite Matching Probability

If the matched features are \(f_1, f_2, \dots, f_k\), their probabilities are combined multiplicatively:

$$
p_{\text{joint}} = \prod_{i=1}^{k} p_{f_i}
$$

Equivalently, computation can be performed in log space:

$$
\log p_{\text{joint}} = \sum_{i=1}^{k} \log p_{f_i}
$$

This formulation allows multiple individually weak matches to accumulate into strong evidence when their joint occurrence is unlikely.

---

##### Composite Collision Probability

The expected number of random report pairs sharing the observed feature pattern can then be approximated using a birthday-problem style collision calculation. Given an anchor population of size \(N_{\text{anchor}}\), the expected number of random report pairs matching the **entire set of observed features simultaneously** is approximated as:
$$
E_{\text{joint}} =
\frac{N_{\text{anchor}}(N_{\text{anchor}}-1)}{2}\, p_{\text{joint}}
$$

This approximates the expected number of report pairs within the anchor population that would share the full set of matched feature values by chance.

---

##### Composite Matching Evidence

The overall matching evidence is then defined as:

$$
L_{\text{match}} =
\max\left(0,\,-\log_{10}(E_{\text{joint}}+\varepsilon)\right)
$$

where \(\varepsilon\) is a small constant used to avoid numerical instability.

The value \(L_{\text{match}}\) increases as the expected number of random matches decreases, meaning that rarer feature combinations produce stronger evidence.

---

#### Determining Domain Size \(D_f\)

For features without empirical frequency estimates, the domain size \(D_f\) represents the number of plausible values for the feature at the observed resolution.

The domain size depends on:

- the feature type
- the resolution of the matched value
- the clinically plausible value range
- the temporal context when applicable

---

##### Dates

For date features, domain size depends on the recorded resolution and the reporting context. For matched **clinical** dates, domain size additionally depends on the local temporal gap to the nearest more recent matched clinical date already accounted for.

###### Context-dependent defaults

The default temporal window depends on the reporting context. When the reporting context is **country-year**, a narrower 1.5-year window is used because most reports in a given country-year are temporally concentrated. For broader contexts (country or global), a 10-year window is used — although the database spans nearly 50 years, exponential growth means most reports have been submitted in the most recent decade.

| Reporting context | Default window | Exact date \(D_f\) | Month–year \(D_f\) | Year only \(D_f\) |
| ----------------- | -------------- | ------------------- | ------------------- | ------------------ |
| Country-year      | 1.5 years      | 540                 | 18                  | 2                  |
| Country or global | 10 years       | 3650                | 122                 | 10                 |

###### Sequential local-gap rule for clinical dates

Matched clinical dates contribute evidence in proportion to the *incremental* temporal extent each one establishes, not to the global temporal span of the pair. The unique matched clinical dates are sorted from most recent to oldest. The first (most recent) date is evaluated under the context-dependent default. Each subsequent date is evaluated under a domain equal to twice the temporal gap to the nearest more recent already-accounted-for matched date, expressed in the unit of that date's own resolution (days / months / years).

This naturally downweights tightly clustered additional dates — they contribute only modest extra evidence — while dates temporally far from anything already matched contribute more.

*Example.* Matched clinical dates `2022-05-20`, `2022-05-18`, `2022-05-15`, `2019-01-01` (day resolution, country-year context). Sorted most-recent-first:

| Date         | Gap to previous | Domain \(D_f\) |
| ------------ | --------------- | -------------- |
| 2022-05-20   | —               | 540 (default)  |
| 2022-05-18   | 2 days          | 4              |
| 2022-05-15   | 3 days          | 6              |
| 2019-01-01   | 1230 days       | 2460           |

Report dates and other demographic dates no longer contribute to this sizing — only unique matched clinical dates do. A late report date therefore cannot inflate the domain used to score clinical-date coincidences.

---

##### Age

Age domain size depends on the resolution of the recorded age.

| Representation    | Example  | Domain size                                                   |
| ----------------- | -------- | ------------------------------------------------------------- |
| Age in years      | 54       | plausible ages 0–80 → \(D_f = 80\)                           |
| Age in months     | 7 months | plausible infant months → \(D_f = 100\)                      |
| Age with decimals | 54.2     | \(D_f = 80 / \text{resolution}\) (e.g. 0.1 years → \(D_f = 800\)) |

---

##### Sex

Sex is treated as a categorical variable.

$$
D_f = \text{number of possible categories}
$$

In the simplest model (we are aware that a better approach would be to account for the relative frequencies of male and female which differ, but in practice, matching patient sex will carry very little evidentiary value either way):

$$
D_f = 2
$$

---

##### Height

For height, the domain size depends on the patient’s age group and the measurement resolution. Children are defined as patients aged ≤15 years.

| Age group         | Resolution | Domain size      |
| ----------------- | ---------- | ---------------- |
| Child (≤15 years) | 1 cm       | \(D_f = 20\)     |
| Adult (>15 years) | 1 cm       | \(D_f = 40\)     |
| Child (≤15 years) | 0.1 cm     | \(D_f = 200\)    |
| Adult (>15 years) | 0.1 cm     | \(D_f = 400\)    |

Higher measurement precision increases domain size and therefore increases the evidential value of an exact match.

---

##### Weight

Weight is handled analogously to height.

| Age group         | Resolution | Domain size      |
| ----------------- | ---------- | ---------------- |
| Child (≤15 years) | 1 kg       | \(D_f = 20\)     |
| Adult (>15 years) | 1 kg       | \(D_f = 40\)     |
| Child (≤15 years) | 0.1 kg     | \(D_f = 200\)    |
| Adult (>15 years) | 0.1 kg     | \(D_f = 400\)    |

Higher measurement precision increases domain size and therefore increases the evidential value of an exact match.

---

##### Drug Sets and Adverse Event Sets

For drug sets and adverse event sets, the probability is estimated as a **leave-pair-out marginal** of the feature, evaluated at the narrowest reporting scope that contains evidence of the feature *outside* the two focal reports. The scopes are tried in order — **primary → country → global** — and a per-report **floor** is used only when no scope has any extra-pair occurrence. This avoids self-inclusion bias from the two focal reports and prevents zero-probability pathologies when the focal pair is the only occurrence of the feature in the primary context.

Drug sets and AE sets are evaluated only when they are **not part of the anchor definition**, to avoid double-counting anchor information.

###### Leave-pair-out ratio

For a given scope with \(n_{\text{total}}\) reports of which \(n_{\text{feature}}\) match the feature, the leave-pair-out marginal is the bare ratio after removing the two focal reports from both numerator and denominator:

$$
\hat{p} \;=\; \frac{n_{\text{feature}} - 2}{n_{\text{total}} - 2}
$$

This is defined and informative only when \(n_{\text{feature}} > 2\) (i.e. at least one report outside the focal pair carries the feature) and \(n_{\text{total}} > 2\).

###### First-level-with-evidence cascade

Walk the scopes from narrowest to broadest and return the bare LPO ratio at the **first scope where the feature has any extra-pair evidence**:

1. **Primary** (the reporting context determined for the pair): if \(n_{\text{feature}} > 2\) and \(n_{\text{total}} > 2\), use \(p_F = (n_{\text{feature}} - 2) / (n_{\text{total}} - 2)\).
2. **Country** (when country counts are supplied and primary had no extra-pair evidence): if \(N_{\text{feature}}^{\text{country}} > 2\) and \(N_{\text{total}}^{\text{country}} > 2\), use \(p_F = (N_{\text{feature}}^{\text{country}} - 2) / (N_{\text{total}}^{\text{country}} - 2)\).
3. **Global** (when global counts are supplied and country had no extra-pair evidence): if \(N_{\text{feature}}^{\text{global}} > 2\) and \(N_{\text{total}}^{\text{global}} > 2\), use \(p_F = (N_{\text{feature}}^{\text{global}} - 2) / (N_{\text{total}}^{\text{global}} - 2)\).
4. **Floor** (no scope has extra-pair evidence): \(p_F = 1 / \max(N_{\text{total}}^{\text{global}} - 2,\; 1)\) when the global total is available, else \(p_F = 10^{-6}\) (`MARGINAL_FLOOR`).

Scopes whose counts are not supplied by the data layer are skipped. The \(-2\) leave-pair-out subtraction is applied at whichever scope is used: the focal pair sits inside country-year ⊂ country ⊂ global, so it is removed from numerator and denominator at whatever level the cascade lands on.

###### Design Rationale

- **Leave-pair-out at every used scope** prevents the focal pair from inflating its own evidence.
- **Bare LPO ratio (no Bayesian smoothing)** is the natural estimate once extra-pair evidence exists at some scope. An earlier recursive Bayesian-smoothed ladder pulled each level's posterior toward the level below by a factor \(\alpha / (n_{\text{total}} + \alpha)\); for a feature unique to the focal pair globally this drove the estimate to \(\sim 10^{-16}\), ten orders of magnitude **below** the intended per-report floor — over-weighting the feature as duplicate evidence (\(+16\) to \(L_{\text{match}}\) instead of \(+6\)).
- **Narrow-first cascade** uses the most specific evidence available: country-year primary rates are the right unit for the duplicate-pair likelihood; broader scopes are consulted only when primary is silent.
- **The floor** guarantees a strictly positive probability when no scope contains evidence outside the focal pair. With the global-derived floor, a feature seen only in the focal pair lands at roughly the per-report rate of the database (\(\sim 1/N_{\text{total}}^{\text{global}}\)); with the constant fallback it lands at \(10^{-6}\), contributing roughly 6 to \(L_{\text{match}}\) instead of vanishing.

###### Worked Example

Suppose the matching drug set is rare, the focal pair is the only country-year occurrence, and the data layer supplies broader-context counts:

- Primary (country-year): \(N_{\text{drugs}} = 2\), \(N_{\text{total}} = 1{,}000\) (only the focal pair has these drugs in this country-year).
- Country: \(N_{\text{drugs}}^{\text{country}} = 5\), \(N_{\text{total}}^{\text{country}} = 10{,}000\).
- Global: \(N_{\text{drugs}}^{\text{global}} = 80\), \(N_{\text{total}}^{\text{global}} = 1{,}000{,}000\).

Walking the cascade:

- Primary: \(N_{\text{drugs}} = 2\) → no extra-pair evidence, skip.
- Country: \(N_{\text{drugs}}^{\text{country}} = 5 > 2\), so

$$
p_F = \frac{5 - 2}{10{,}000 - 2} \;\approx\; 3.0 \times 10^{-4}
$$

The country scope captures the fact that the drug set is rare but not vanishingly so; the global scope is never consulted. If the drug set had been unique to the focal pair at the country level as well (\(N_{\text{drugs}}^{\text{country}} = 2\)) but observed 80 times globally, the cascade would have fallen through to global and yielded \((80-2)/(1{,}000{,}000-2) \approx 7.8 \times 10^{-5}\). If unique at every scope, it would have landed at the floor \(1/(1{,}000{,}000 - 2) \approx 1.0 \times 10^{-6}\) — never below.

This logic applies symmetrically to the AE set (with the AE-set counts at each level) and the same formula is used regardless of which of drugs / AEs forms the anchor.

---

##### Clinical Dates (Drug Start/End, AE Onset, Overall AE Onset)

Matching dates from drug, adverse event, and overall AE onset records provide additional evidence for the collision probability. This uses **explicit date-field coincidence** only — treatment intervals are NOT expanded into sets of intermediate dates.

###### Which dates are compared

For each matched drug (shared between the two reports), the following date fields are compared:

- **start date** ↔ **start date**
- **end date** ↔ **end date**

For each matched adverse event, the following field is compared:

- **onset date** ↔ **onset date**

Additionally, the **overall AE onset date** is compared at the report level. This is a case-level date (either explicitly reported or derived — see the Clinical Date Inconsistencies section) and does not require shared AE terms between the two reports.

Only same-field-type comparisons count. A drug's start date on one report must NOT match against the same drug's end date on the other report. Dates from unmatched drugs or unmatched AEs are not considered.

Matching is **role-agnostic**: a drug recorded as `suspected` in one report and `concomitant` in the other is still compared, since the drug set intersection itself is role-agnostic. If the same substance appears more than once within a single report (e.g. listed under different roles, or split across structured and narrative entries), every pair of same-substance entries is compared — a dateless duplicate does not shadow a dateful one.

Standard partial-date matching rules apply: month-only and year-only values match at their coarser resolution.

###### Deduplication to unique dates

The result is collapsed to a set of **unique matched dates**. If the same calendar date matches for multiple drugs, multiple AEs, or multiple field types, it contributes only once (at the finest matched resolution).

A coarser match is also **subsumed** by a finer match whose value falls inside it: when both `2021-11-08` (day) and `2021-11` (month) appear, or `2000-04-08` (day) and `2000` (year), only the finer entry is kept. The coarser value is the truncated view of the same calendar coincidence and must not contribute an independent reward. Coarser matches are retained only when they stand apart from every finer match (e.g. a year-only `2022` alongside a day `2021-11-08`).

Each unique matched date contributes \(p_f = 1/D_f\) to the composite probability. The most recent matched date is sized by the context-dependent default; each older matched date is sized by the sequential local-gap rule (see § Dates above).

###### Example

Matched drugs: cyclophosphamide, mitoxantrone, vincristine. Matched AE: arrhythmia.

| Entity             | Field | Report 1   | Report 2   | Match?                               |
| ------------------ | ----- | ---------- | ---------- | ------------------------------------ |
| cyclophosphamide   | start | 1994-03-30 | 1994-03-30 | **Yes** — same start date            |
| mitoxantrone       | start | 1994-03-30 | 1994-05-12 | No — different start dates           |
| mitoxantrone       | end   | 1994-05-12 | (missing)  | No — end date missing on one side    |
| vincristine        | start | 1994-03-30 | 1994-05-12 | No — different start dates           |
| vincristine        | end   | 1994-05-12 | (missing)  | No — end date missing on one side    |
| arrhythmia         | onset | 1994-05-12 | 1994-05-12 | **Yes** — same onset date            |

Unique matched dates: **{1994-03-30, 1994-05-12}** — count = 2.

Note that for mitoxantrone, Report 1's end date (1994-05-12) equals Report 2's start date (1994-05-12), but this cross-field coincidence is NOT counted. The date 1994-05-12 enters the matched set only through the arrhythmia onset match.

---

#### Interpretation

This framework evaluates how surprising the **entire pattern of observed matches** would be within the anchor population.

Individually weak matches may accumulate into strong evidence when their joint probability is sufficiently small. Conversely, matches that are common within the anchor population contribute little or no evidence.

### Narrative Consistency Assessment - INDICATIVE

(This is a placeholder for now, indicating intent - we are not yet positioned to implement this component)

Pharmacovigilance reports frequently contain narrative descriptions that provide clinical context not fully captured in structured database fields.

Structured clinical attributes extracted from narratives are incorporated during the earlier data extraction stage and therefore already contribute to the structured feature matching process.

In addition to these structured values, the narratives of the two reports are compared directly to assess whether they describe a clinically consistent scenario.

---

#### Narrative Comparison

The narrative texts of the two reports are analyzed using a language model to evaluate whether they describe compatible clinical events.

The model assigns one of four qualitative categories:

| Category            | Interpretation                                               |
| ------------------- | ------------------------------------------------------------ |
| strongly consistent | narratives clearly describe the same clinical scenario       |
| consistent          | narratives appear compatible and may plausibly describe the same patient |
| neutral             | narratives provide insufficient information to assess similarity |
| inconsistent        | narratives contain information suggesting different patients |

Examples of strongly consistent narratives include:

- nearly identical clinical histories
- matching sequences of treatments and adverse events
- descriptions of the same clinical episode with similar timing

Examples of inconsistent narratives include:

- incompatible clinical timelines
- descriptions of clearly different clinical circumstances
- explicit reference to different patient identifiers within the same clinical study

---

#### Output

The narrative comparison stage produces a categorical narrative consistency assessment.

Example:

```json
{
  "narrative_consistency": "consistent"
}
```

This output is later interpreted together with the matching evidence and detected inconsistencies in the decision logic.

### AE clinical-picture inconsistency

A dedicated component evaluates whether the two reports describe a clinically inconsistent **adverse-event picture** — independently of chronology, which is handled by the rule-based clinical-date inconsistency module. The motivation is that two reports can share dates yet describe clinically different AE pictures (and conversely differ in wording while describing the same picture); rule-based set comparison alone cannot distinguish these.

Two interchangeable assessors produce the same `AEClinicalPictureResult` shape:

* **LLM-assisted** — the original component; uses an LLM to read AE lists plus narratives and emit clinically grouped findings.
* **Rule-based / deterministic** — a non-LLM fallback for organisations or settings where LLM use is not possible. Uses the primary MedDRA hierarchy alone (PT / primary HLT / primary HLGT / primary SOC).

`PipelineRunner.ae_clinical_picture_mode` (`"llm"` | `"rule_based"` | `"auto"`, default `"auto"`) selects between them; `"auto"` uses the LLM when a client is configured and falls back to the rule-based assessor otherwise (and on LLM exception). `ae_clinical_picture_method` on `PairFeatures` records which path produced the result.

#### LLM-assisted assessor — output shape

The LLM returns a list of distinct, clinically non-redundant findings rather than a single global label. Each finding carries:

- `kind` ∈ {`unmatched_cluster`, `matched_characterization`, `global_picture_mismatch`}
- `severity` ∈ {`minor`, `moderate`, `strong`, `decisive`}
- a short `rationale`, the involved AE `terms`, and an optional human `cluster_label`

The findings are post-processed by a deterministic dedup step: when any finding has `kind = global_picture_mismatch` and `severity = decisive`, all other findings are suppressed from the severity counts. Suppressed findings remain in the master inconsistency list (with `suppressed = true` and a `suppressed_by` reason) so reviewers can see the full LLM reasoning.

#### LLM-assisted assessor — severity rubric

| Severity | Interpretation |
| --- | --- |
| minor | small, peripheral, or compatible-but-broader difference |
| moderate | clinically meaningful non-redundant difference, not dominant |
| strong | substantial difference materially weakening same-case interpretation |
| decisive | no shared / compatible AEs; clearly different pictures at the case level |

The model is instructed to anti-overcount: group related unmatched AEs into a single finding, tolerate a few unmatched terms in long lists with broad overlap, treat one-sided subset-style differences leniently, and avoid stacking subordinate findings under a decisive global mismatch.

#### Rule-based assessor — algorithm

Inputs: each `AdverseEvent` carries the primary MedDRA hierarchy (`primary_hlt_name`, `primary_hlgt_name`, `primary_soc_name`). All hierarchy-name comparisons are case-folded; empty / missing values never match. The rule-based assessor mimics the *spirit* of the LLM prompt while remaining deterministic and auditable.

**Step 1 — global clinical picture overlap (symmetric, includes Investigations PTs).**
Find the most granular shared level between any reported PT in report 1 and any in report 2:

| Most granular overlap | Severity |
| --- | --- |
| exact PT match | none |
| shared primary HLT | minor |
| shared primary HLGT | moderate |
| shared primary SOC | strong |
| no shared primary SOC | decisive |

Step 1 deliberately uses *all* reported PTs, including PTs in the Investigations SOC, and looks for any shared clinical core (not a dominant/majority-level approach).

**Step 2 — directional unmatched clinical content (computed for both `R1 → R2` and `R2 → R1`, excludes Investigations PTs).**
For source report A and target report B, filter out PTs whose primary SOC is Investigations on both sides. Then for each remaining source PT:

- exact PT match in target → counts as `M`
- else, source HLT present in target's HLT set → counts as `M` (clinically compatible coding difference)
- else, source HLT non-empty and absent from target → counts as `U` (unmatched)
- else (source HLT missing) → counts as `M` (cannot claim "unmatched" without hierarchy info; fail-safe)

Severity:

| Condition | Severity |
| --- | --- |
| `U == 0` | none |
| `U == 1` | minor |
| `U / (U + M) < 0.5` | minor |
| otherwise | moderate |

Step 2 intentionally counts **unmatched PT burden**, not clinically distinct unmatched clusters, so PTs that differ at PT level but share an HLT do not count as unmatched. Bidirectional unmatched burden therefore naturally produces a larger penalty than a simple subset relation. Investigations SOC PTs are excluded from both `U` and `M` for conceptual consistency, but Step 1 still considers them so that a shared lab-finding overlap is not lost.

The full Step-1 / Step-2 audit (overlap level, directional U/M counts, Investigations exclusion flag, missing-hierarchy warnings) is persisted on `PairFeatures.ae_clinical_picture_rule_based_details` and surfaced in the pair-review HTML.

#### Assessment status

Findings are accompanied by a status that distinguishes "did look" from "did not look":

| `ae_clinical_picture_status` | `ae_clinical_picture_reason` | `ae_clinical_picture_method` | When |
| --- | --- | --- | --- |
| `assessed` | `""` | `llm` | LLM was called and produced a verdict |
| `assessed` | `rule_based` | `rule_based` | Deterministic assessor ran (`mode="rule_based"`, or `mode="auto"` with no LLM client and hierarchy present) |
| `assessed` | `llm_fallback_to_rule` | `rule_based` | `mode="auto"`: LLM raised → fell back to deterministic assessor |
| `not_assessed` | `no_llm_client` | `llm` | `mode="llm"` and no LLM client configured |
| `not_assessed` | `trivially_matched` | `llm` | normalised AE PT sets equal AND both narratives empty (LLM call skipped — no information to assess) |
| `not_assessed` | `llm_error` | `llm` | `mode="llm"`: LLM call raised an exception |
| `not_assessed` | `missing_hierarchy` | `rule_based` | rule-based path, but no PT on either report carries MedDRA hierarchy info |

Downstream code must treat `not_assessed` as *no information*, not as "assessed and clean".

#### Integration with PairFeatures

Findings from either assessor are appended to the unified `PairFeatures.inconsistencies` list. The `source` distinguishes them: `"llm_ae_clinical_picture"` vs. `"rule_ae_clinical_picture"`. Severity counts (excluding suppressed LLM entries) are merged into `inconsistency_counts` alongside the rule-based and clinical-date counts that already feed the weighted inconsistency score. Per-component counts are also persisted (`ae_clinical_picture_counts`) for auditability and review surfacing.

Final duplicate classification logic remains rule-based and probability-anchored: this assessment feeds into the existing programmed adjudication via `inconsistency_counts`, never replacing it.

### Inconsistency Detection

In addition to matching evidence, the method evaluates information that may indicate that two reports describe different patients.

Because pharmacovigilance data often contain recording errors or incomplete information, inconsistencies do not automatically imply that reports refer to different individuals. Instead, they are recorded and later interpreted within the decision logic.

---

#### Inconsistency Representation

Each detected inconsistency is stored as a structured object describing:

- the feature involved
- the observed difference
- the severity level

Example:

```json
{
  "feature": "height",
  "difference": 7,
  "units": "cm",
  "severity": "moderate"
}
```

The complete set of inconsistencies for a report pair is stored as:

```json
{
  "inconsistencies": [
    {...},
    {...}
  ]
}
```

The number of inconsistencies of different types can then be derived when applying the decision logic.

------

#### Severity Levels

Three levels of inconsistency severity are defined:

| Severity | Interpretation                                          |
| -------- | ------------------------------------------------------- |
| moderate | difference that weakens the duplicate hypothesis        |
| strong   | difference that strongly suggests different individuals |
| decisive | hard contradiction that precludes a `Duplicates` verdict |

Moderate and strong inconsistencies are derived from structured data comparisons (age, sex, height, weight, country of origin, drug set). Their thresholds are based on typical variability and recording errors in pharmacovigilance data. The current thresholds specified below are starting values and may need refinement. It's possible that they should be adaptive (e.g. account for patient age when assessing mismatching height and weight). Since recording errors or misclassification are known to occur for any fields on adverse event reports, strong mismatches are not considered decisive on their own and must be interpreted together with the strength of the matching evidence via the weighted inconsistency score (see Decision logic below).

Decisive inconsistencies are expected to originate from narrative analysis (e.g. distinct patient identifiers mentioned in case narratives from the same study). When present, they preclude a `Duplicates` verdict; the surrounding matching evidence then determines whether the pair is surfaced as `Otherwise related` (when independence is still rejected) or `Unrelated` (otherwise) — see § Decision logic.

------

#### Structured Data Inconsistencies

##### Age

Age differences are evaluated using the absolute difference in years.

| Difference | Classification        |
| ---------- | --------------------- |
| > 0 years  | minor inconsistency   |
| ≥ 2 years  | moderate inconsistency |
| ≥ 5 years  | strong inconsistency  |

------

##### Height

Height differences are evaluated in centimetres.

| Difference | Classification        |
| ---------- | --------------------- |
| > 0 cm     | minor inconsistency   |
| ≥ 3 cm     | moderate inconsistency |
| ≥ 6 cm     | strong inconsistency  |

------

##### Weight

Weight differences are evaluated in kilograms.

| Difference | Classification        |
| ---------- | --------------------- |
| > 0 kg     | minor inconsistency   |
| ≥ 3 kg     | moderate inconsistency |
| ≥ 10 kg    | strong inconsistency  |

------

##### Sex

A mismatch in recorded patient sex is treated as a strong inconsistency:

```
sex_R1 ≠ sex_R2
```

------

##### Country of origin

Country of origin is treated as a categorical variable. A mismatch is classified as a **minor** inconsistency:

```
country_R1 ∩ country_R2 = ∅   →   minor inconsistency
```

Cross-country duplication does occur (e.g. a case originally reported in one country may resurface in another), so the mismatch is given limited weight in the weighted inconsistency score. Surfacing it as a minor inconsistency keeps reviewers aware of the discrepancy without materially suppressing duplicate candidates that differ only by reporting country. As with sex, missing values on either side produce no inconsistency, and any shared country value between the two reports removes the inconsistency.

------

##### Drug Set

Drug-set inconsistencies detect when a clinically important drug from one report is entirely absent from the other report's drug list.

Drugs are classified by their **role** in the report:

| Role | Classification |
| ---- | -------------- |
| suspected | Key drug |
| interacting | Key drug |
| concomitant | Non-key (background medication) |
| None / unknown | Non-key |

The check is **directional**: for each report, its key drugs are compared against the full drug list (all roles) of the other report. Each direction produces at most one moderate inconsistency, regardless of how many key drugs are missing.

```
For each direction (r1→r2, r2→r1):
  key_drugs(source) = substances with role ∈ {suspected, interacting}
  all_drugs(target) = all substances regardless of role
  missing = key_drugs(source) − all_drugs(target)
  if missing is non-empty → 1 moderate inconsistency
```

This yields 0, 1, or 2 moderate inconsistencies per pair.

**Design notes:**

- Drug name matching is **case-insensitive**.
- A drug present in both reports with **different roles** is not inconsistent — role differences do not indicate different individuals.
- Reports with **no key drugs** (all concomitant or role unknown) produce no drug-set inconsistencies.
- Only drug **presence** is evaluated; differences in dosage, form, or dates are not considered here — date differences are handled by the Clinical Date Inconsistencies module below.

-------

##### Clinical Date Inconsistencies

Clinical-date inconsistencies are detected by a dedicated rule-based module (`feature_generation/clinical_date_inconsistencies.py`). This replaces the earlier combined drug-date score with fine-grained, severity-bucketed counts.

**Compared entities**

- **Overall AE onset date** (one per report — highest priority; either an explicitly reported case-level date or, when absent, a derived value)
- **Drug start dates** (matched by normalized substance name)
- **AE onset dates** (matched by normalized PT name)
- **Drug end dates** — downgrade-only evidence, never primary inconsistency sources

**Counterpart selection — alignment across repeated entries**

The same normalized drug substance may appear on a single report with multiple distinct start dates (for example, re-challenge regimens or different dosing periods), and the same MedDRA PT may appear with several distinct onset dates. Comparing all cross-pairs would produce spurious "strong" findings whenever the reports disagree on which regimen is primary.

Instead, for each matched entity (one normalized drug substance or one PT appearing on both reports), counterpart dates are chosen as follows:

1. **Group by normalized entity.** All drug start dates for one substance on one report form a group; likewise for AE onsets on one PT.
2. **Deduplicate exact-duplicate date values within a report.** Two rows for the same substance with the same start date collapse into one slot. When two rows share a date value but differ in drug role, a non-concomitant role dominates over a concomitant one for that slot — so a drug that is both suspected and concomitant at the same date is not penalised with the concomitant-drug downgrade.
3. **Compute a minimum-cost one-to-one alignment** between the two reports' date lists for that entity. The alignment cost is the interval-aware minimum possible day difference used by the severity thresholds. Ties are broken deterministically.
4. **Emit candidate findings only from aligned pairs.** Leftover unmatched dates (on the larger side) do **not** generate additional findings — they are simply absent from the comparison, consistent with the missing-data rule.

Drug **role is ignored** when choosing counterparts: alignment minimises date distance only. The source-side role is retained, however, so the concomitant-drug downgrade still applies per direction when the source entry is concomitant.

After alignment, the downstream pipeline (classification, downgrades, source-date bucketing, cross-direction deduplication) is unchanged. Cross-direction deduplication uses a **pair-aware** key — `(entity_label, {source_date, matched_date})` — so that when an entity produces several aligned pairs (for example, two regimen start-date pairs for the same drug), each pair is deduplicated independently instead of collapsing all pairs into one.

**Overall AE onset date — explicit vs. derived**

The pipeline uses a single canonical "effective" overall AE onset date per report, resolved as follows:

1. If `Report.reported_overall_ae_onset_date` is present (an explicit case-level date supplied by the loader, e.g. extracted from a `CASE EVENT DATE: YYYYMMDD` narrative pattern on US reports), it is used as-is — its `PartialDate` resolution is trusted.
2. Otherwise, the derivation rule below produces a fallback value.

This logic is encapsulated in `effective_overall_ae_onset_date(report)` in `feature_generation/clinical_date_inconsistencies.py`; downstream consumers should call it rather than the derivation directly.

**Overall AE onset date derivation (fallback)**

The goal is to pick the earliest AE onset date and only refrain from
doing so when there is *clear evidence* that an early onset is
incompatible with the suspected drugs. The rule deliberately errs on
the side of keeping early AE onsets — coarse-resolution drug start
dates or re-challenge regimens should not cause a plausibly valid AE
onset to be rejected.

For each report:

1. If no AE onset dates exist: missing (not compared).
2. If no suspected-drug start dates exist: the earliest AE onset date
   (unconditional fallback).
3. Otherwise, group suspected-drug start dates by normalized substance
   name and iterate AE onsets in earliest-first order. Pick the
   first AE onset that is **compatible** with every suspected
   substance.

An AE onset `ae` is **compatible** with a substance when *any* of
that substance's regimen start dates `r` satisfies
`earliest_plausible(r) ≤ latest_plausible(ae)` — that is, it is
plausible the drug had already been started by the time the AE
occurred. Grouping by substance ensures that a re-challenge (two
regimens of the same drug, one before and one after the AE) keeps
the AE in the candidate set via the earlier regimen.

Consequences:

* A month-resolution suspected drug start such as `1968-11` does
  **not** disqualify a day-resolution AE onset `1968-11-12` — the
  drug could plausibly have started on `1968-11-01`.
* A day-resolution drug start of `2020-02-01` does disqualify an AE
  onset of `2020-01-15`, because the AE is unambiguously before the
  drug's earliest plausible start.

If no AE onset is compatible with every suspected substance, the
derivation returns missing.

**Severity thresholds**

Overall AE onset date (tighter):

| Difference | Severity |
| ---------- | -------- |
| 0 days | none |
| 1–7 days | minor |
| 8–30 days | moderate |
| > 30 days | strong |

Drug start dates and AE onset dates (capped at moderate):

| Difference | Severity |
| ---------- | -------- |
| 0–7 days | none |
| 8–69 days | minor |
| ≥ 70 days | moderate |

Per-entity drug start and AE onset mismatches never escalate to *strong*: a single regimen or PT-level disagreement is too noisy on its own to constitute strong evidence. Case-level temporal disagreement is captured by the overall AE onset date thresholds above.

**Incomplete dates**

Partial dates are converted to intervals (`[earliest, latest]` plausible date). The *minimum possible* day difference between two intervals is used. A severity is only assigned when the category is guaranteed even under the most generous interpretation — this avoids false positives at the cost of occasionally missing a discrepancy when resolution is too coarse.

**Downgrade rules**

1. *Cross-entity downgrade*: if the source date exactly matches any clinically relevant date (drug start, AE onset, overall AE onset, or drug end date) on the other report, the severity is reduced by one step (strong → moderate, moderate → minor, minor stays minor).

2. *Concomitant drug downgrade*: for concomitant-drug start date mismatches, severity is reduced by one step (moderate → minor, minor → ignored). Because per-entity drug start severity is capped at moderate, a two-step downgrade would always wipe concomitant findings out entirely; a single-step downgrade keeps concomitant moderates contributing as minor inconsistencies.

**One inconsistency per source date**

Each unique source date on a report contributes at most one counted inconsistency. When multiple entities share the same date, priority determines which is counted: overall AE onset > suspected-drug start > AE onset > concomitant-drug start. If the highest-priority entity has no inconsistency, a lower-priority entity may contribute.

**Decisive impossibility**

If a report date on one report is strictly before the earliest plausible overall AE onset on the other report, a decisive inconsistency is counted. This applies even with incomplete dates — e.g. report date `1999-02-10` vs onset year `2005` is decisive because the report predates even the earliest plausible onset.

**Symmetry and cross-direction deduplication**

The comparison is evaluated from both directions (report 1 → report 2 and report 2 → report 1), with source-date bucketing applied independently per report. After bucketing, a cross-direction deduplication step ensures that each entity (e.g. the same drug on both reports) produces at most one counted inconsistency. When both directions yield a finding for the same entity, the one with the lower final severity is kept — this honours direction-specific downgrades conservatively.

**Integration with the decision logic**

Clinical-date inconsistency counts are merged into the main `inconsistency_counts` on `PairFeatures` and thus contribute to the weighted inconsistency score $w_{\text{tot}}$ used by the decision logic. The separate `clinical_date_inconsistency_counts` field is retained for auditability and detailed reporting.

**Output**

The module produces `ClinicalDateInconsistencyCounts` with `n_minor`, `n_moderate`, `n_strong`, `n_decisive`, exposed on `PairFeatures.clinical_date_inconsistency_counts`. Individual selected findings are exposed on `PairFeatures.clinical_date_findings` for human review.

------

#### Narrative-Derived Inconsistencies

Narratives may contain explicit information indicating that the reports refer to different individuals.

Examples include:

- references to different patient identifiers within the same clinical study
- clearly incompatible clinical timelines
- explicit statements describing different patients

When such contradictions are identified, they are recorded as strong inconsistencies.

Example:

```
{
  "feature": "narrative_identifier",
  "description": "different patient IDs in same study",
  "severity": "strong"
}
```

------

#### Interpretation

The set of detected inconsistencies represents evidence against the duplicate hypothesis.

These inconsistencies are not interpreted in isolation but are evaluated together with the matching evidence and anchor population within the decision logic.

For example:

- strong matching evidence may outweigh a single strong inconsistency
- multiple strong inconsistencies may indicate that reports describe different patients
- moderate inconsistencies may be tolerated when matching evidence is strong

## Decision logic

The final adjudication of a report pair is determined by jointly evaluating:

- the strength of the **rarity-based matching evidence** (\(L_{\text{match}}\)),
- the presence and severity of **detected inconsistencies**, and
- the **narrative consistency assessment**.

The decision logic balances positive evidence (the matched features being
unlikely under an *independence* null) against contradictory evidence
(structured or narrative-derived inconsistencies).

Internally, the decision logic produces two layered labels:

* an **independence assessment** — whether the matching evidence is
  strong enough to reject the null that the two reports are independent
  draws from the comparison population
  (`Non-independent / Independent`); and
* when independence has been rejected, a **duplicate relationship**
  sub-label that uses the inconsistency signals to distinguish a
  likely-duplicate case from one linked by something other than being
  the same individual (`Duplicates / Otherwise related`).

These map to the user-facing label vocabulary as follows:

| Independence assessment | Duplicate relationship | User-facing final label |
|---|---|---|
| Non-independent | Duplicates | **Duplicates** |
| Non-independent | Otherwise related | **Otherwise related** |
| Independent | — | **Unrelated** |

The two-layer split is deliberate: a low expected-collision count is
evidence *against independence*, not specifically *for same individual*.
Same reporter, same study, or unusual shared context can all produce
strong matching evidence without the reports describing the same
patient. The duplicate vs otherwise-related distinction is therefore
handled by the inconsistency component once independence has been
rejected. The mapping above is implemented by
`ClassificationResult.final_label` and is the only label that downstream
consumers (eval, review HTML, secure repo) should rely on.

---

### Inputs to the decision logic

For each report pair, the following quantities are provided as inputs:

**Matching evidence**

$$
L_{\text{match}}
$$

which quantifies how unlikely the observed combination of matched features would be within the anchor population.

**Weighted inconsistency score**

$$
w_{\text{tot}} = w_{mi} \times n_{\text{minor}} + w_m \times n_{\text{moderate}} + w_s \times n_{\text{strong}} + w_d \times n_{\text{decisive}}
$$

which combines minor, moderate, strong, and decisive inconsistencies into a single score. The counts include all sources: demographic inconsistencies (age, weight, height, sex), drug-set inconsistencies, clinical-date inconsistencies, and narrative-derived inconsistencies. The weights \(w_{mi}\), \(w_m\), \(w_s\) and \(w_d\) are configurable (defaults: \(w_{mi} = 0.4\), \(w_m = 1\), \(w_s = 3\), \(w_d = 100\)). The decisive weight is deliberately set large enough that any decisive count always exceeds every zone tolerance; a configuration validator enforces this invariant.

**Narrative consistency category**

One of:

- strongly consistent
- consistent
- neutral
- inconsistent

---

### Weighted inconsistency score

Minor, moderate, strong, and decisive inconsistencies are combined into a single weighted score:

$$
w_{\text{tot}} = w_{mi} \times n_{\text{minor}} + w_m \times n_{\text{moderate}} + w_s \times n_{\text{strong}} + w_d \times n_{\text{decisive}}
$$

where \(w_{mi}\), \(w_m\), \(w_s\) and \(w_d\) are configurable weights. With the current defaults (\(w_{mi} = 0.4\), \(w_m = 1\), \(w_s = 3\), \(w_d = 100\)), a single strong inconsistency contributes 7.5 times as much as a minor one, and a single decisive inconsistency always exceeds every zone tolerance (see § Decisive inconsistencies below).

---

### Decision zones

The decision space is divided into four zones based on the magnitude of \(L_{\text{match}}\). Each zone applies a progressively stricter tolerance for \(w_{\text{tot}}\):

| Zone | Condition | Duplicates if... | Otherwise |
|---|---|---|---|
| Strong evidence | \(L_{\text{match}} > 3.0\) | \(w_{\text{tot}} < 4\) | Non-independent / Otherwise related |
| Moderate evidence | \(L_{\text{match}} > 2.0\) | \(w_{\text{tot}} < 3\) | Non-independent / Otherwise related |
| Weak evidence | \(L_{\text{match}} > 1.0\) | \(w_{\text{tot}} < 1\) | Non-independent / Otherwise related |
| Below threshold | \(L_{\text{match}} \leq 1.0\) | never | Independent (Unrelated) |

In all three non-trivial zones the matching evidence is treated as
strong enough to reject the independence null on its own, so any pair
in these zones is labelled `Non-independent`; the inconsistency score
then decides whether the sub-label is `Duplicates` or `Otherwise
related`. The zones differ only in how much inconsistency evidence is
tolerated before the verdict is downgraded from `Duplicates`. Below the
weak threshold the matching evidence is too weak to reject independence
regardless of how clean the structured comparison looks.

The L\_match zone boundaries (3.0, 2.0, 1.0), the zone-specific \(w_{\text{tot}}\) thresholds (4, 3, 1), and the inconsistency weights are all configurable parameters.

**Examples** (using default parameters):

- \(L_{\text{match}} = 4.2\), one strong inconsistency: \(w_{\text{tot}} = 3 < 4\) → **Non-independent / Duplicates**
- \(L_{\text{match}} = 4.2\), one strong + one moderate: \(w_{\text{tot}} = 4\), not \(< 4\) → **Non-independent / Otherwise related**
- \(L_{\text{match}} = 2.5\), two moderate inconsistencies: \(w_{\text{tot}} = 2 < 3\) → **Non-independent / Duplicates**
- \(L_{\text{match}} = 2.5\), one strong inconsistency: \(w_{\text{tot}} = 3\), not \(< 3\) → **Non-independent / Otherwise related**
- \(L_{\text{match}} = 1.5\), no inconsistencies: \(w_{\text{tot}} = 0\) → **Non-independent / Duplicates**
- \(L_{\text{match}} = 1.5\), one moderate inconsistency: \(w_{\text{tot}} = 1\), not \(< 1\) → **Non-independent / Otherwise related**
- \(L_{\text{match}} = 0.8\): below threshold → **Independent (Unrelated)** regardless of inconsistencies

---

### Decisive inconsistencies

Decisive inconsistencies (e.g. distinct patient identifiers from the same study, identified through narrative analysis) participate in the zone-based logic as a term in \(w_{\text{tot}}\) with a deliberately large weight \(w_d = 100\). This is set high enough that any non-zero decisive count drives \(w_{\text{tot}}\) past every zone tolerance, so the zone's failing-branch outcome is taken. A configuration validator enforces \(w_d > \max(\text{zone thresholds})\) so this invariant is preserved if the thresholds are ever retuned.

The effect is the same as an explicit override, but expressed through the ordinary weighted score — which keeps the breakdown transparent (the decisive contribution is visible in the explanation string and in the review HTML penalty column).

When a decisive inconsistency is present:

- \(L_{\text{match}} > 1.0\) → **Non-independent / Otherwise related**
  (the matching evidence still rejects independence, so the pair is
  surfaced as related but not the same case)
- \(L_{\text{match}} \leq 1.0\) → **Independent (Unrelated)**
  (independence cannot be rejected, so no claim about relatedness is
  made)

In other words, decisive inconsistencies preclude a `Duplicates` verdict — the reports cannot describe the same individual case — but do not by themselves overturn the independence assessment.

---

### How the source/case verdict is computed

The shared-source override below consumes a precomputed `features.source_case_assessment.verdict`. That verdict is produced upstream in two stages, both summarized here; full schemas live in [`data_contracts.md § Derived Narrative Signals`](data_contracts.md).

**Stage 1 — per-narrative extraction.** Each report's narrative is processed by the LLM task `llm_dup_dev.llm.tasks.narrative_source_case`, producing a `NarrativeSourceCase` record. The record carries (a) a list of typed source identifiers — `doi`, `pmid`, `trial_registration`, `study_name`, `protocol_number`, `linked_case`, `article_reference` — each post-normalized by the type-specific normalizer in `feature_generation/source_case_comparison.py`, (b) the `single_patient_source` / `multi_patient_source` booleans the narrative supports, and (c) an optional case-within-source identifier with a typed family (`case` / `patient` / `pt` / `subject`). The extractor is conservative: fields are null whenever the narrative does not give clear support.

**Stage 2 — deterministic pair-level comparison.** `feature_generation/source_case_comparison.compare_source_case` takes two `NarrativeSourceCase` records and decides which of seven verdicts applies. No LLM is involved at this stage. The logic has two parts:

1. **Shared-source detection.** Each side is reduced to a set of `(type, normalized_value)` tuples. If those sets intersect on at least one non-`unknown` type, the most specific shared type (DOI > PMID > trial registration > linked case > protocol number > study name > article reference) is surfaced as `shared_source_identifier`. When strict equality finds nothing, a conservative token-set fallback is tried over `ARTICLE_REFERENCE` pairs only — citations that differ only in punctuation, accents, or supplement / page-range abbreviation style (e.g. `34(2):S136-S137` vs `34 (Suppl 2):S136-7`) are recovered when their lowercase NFKD-folded alphanumeric tokens, minus a small noise vocabulary (`et`, `al`, `vol`, `suppl`, `no`, `iss`, `pp`, …), share Jaccard ≥ 0.85, ≥ 8 non-noise tokens, and an equal 4-digit year when both sides carry one. Strict DOI/PMID/etc. matches always win over this fallback by virtue of the preference order.

2. **Verdict selection.** Given the outcome of step 1 and the `single_patient_source` / `multi_patient_source` / case-identifier signals on each side, exactly one verdict is emitted:

    | Pre-condition | Verdict |
    |---|---|
    | Either extraction missing | `NOT_ASSESSED` |
    | Shared source identifier found, and either side has `single_patient_source=True` | `SAME_SOURCE_SINGLE_PATIENT` |
    | Shared source identifier found, comparable case identifiers exist on both sides, normalized values **agree** | `SAME_SOURCE_SAME_CASE` |
    | Shared source identifier found, comparable case identifiers exist on both sides, normalized values **differ** | `SAME_SOURCE_DIFFERENT_CASE` |
    | Shared source identifier found, but case identifiers are missing on one side, of incompatible family, or otherwise not safely comparable | `SAME_SOURCE_CASE_UNCOMPARABLE` |
    | No shared source identifier, but both sides report `multi_patient_source=True` | `MULTI_PATIENT_CONTEXT_NO_LINK` |
    | Otherwise | `NO_DECISIVE_SIGNAL` |

    Two case identifiers are considered *comparable* iff both carry a non-null non-`unknown` family of the same value (`case` vs `case`, `patient` vs `patient`, …) and both have non-empty normalized values; the comparator never treats family-mismatched or family-missing identifiers as evidence of different individuals.

The verdict, the surfaced `shared_source_identifier` (with its type), and short prose `reasoning` are stored on `PairFeatures.source_case_assessment`. The four `SAME_SOURCE_*` verdicts feed the override described next; the three advisory verdicts (`MULTI_PATIENT_CONTEXT_NO_LINK`, `NO_DECISIVE_SIGNAL`, `NOT_ASSESSED`) do not, and the pair is adjudicated through the ordinary rarity-zone logic.

---

### Shared-source override

A reliable shared source identifier derived from the narratives is itself evidence against independence: if two reports both quote the same DOI (or other stable source identifier), they cannot be treated as independent draws from the comparison population regardless of how rare the matched clinical features are. This case is handled through a dedicated override path in `classifier/decision_logic.py`, separate from the rarity-based \(L_{\text{match}}\) framework.

The override fires whenever `features.source_case_assessment.verdict` is one of the four verdicts that prove a shared source exists:

| Verdict | Sub-label mapping |
|---|---|
| `SAME_SOURCE_SINGLE_PATIENT` | `Duplicates` unless \(w_{\text{tot}} \geq\) `w_tot_med_zone` → `Otherwise_related` |
| `SAME_SOURCE_SAME_CASE`      | `Duplicates` unless \(w_{\text{tot}} \geq\) `w_tot_med_zone` → `Otherwise_related` |
| `SAME_SOURCE_DIFFERENT_CASE` | `Otherwise_related` (pinned) |
| `SAME_SOURCE_CASE_UNCOMPARABLE` | `Otherwise_related` (pinned) |

The two "positive" verdicts default to `Duplicates`, but are downgraded to `Otherwise_related` when non-source-case signals accumulate enough severity to push \(w_{\text{tot}}\) past the medium-zone threshold (default `3.0`). The threshold is shared with the medium L_match zone by design: a reliable shared-source verdict warrants the same inconsistency tolerance as moderate rarity evidence, so a single strong inconsistency — or equivalent accumulated severity (three moderates, one strong + one moderate, …) — is needed to demote. A single moderate or a handful of minors is therefore tolerated. Decisive inconsistencies continue to demote via their large weight (`w_decisive = 100`). The two "negative" verdicts are pinned to `Otherwise_related`:

- `SAME_SOURCE_DIFFERENT_CASE` emits a **decisive** `Inconsistency` (synthesized by `feature_generation/source_case_inconsistency.py`) — two reports sharing a DOI but carrying different comparable case labels clearly aren't the same individual, but they are linked by the shared source. The pin to `Otherwise_related` is driven by the override; the decisive row is informational and audit-facing.
- `SAME_SOURCE_CASE_UNCOMPARABLE` emits **no** `Inconsistency`. The verdict represents *absence of comparable evidence* (a shared source identifier without safely comparable case labels), not a discrepancy, so synthesizing one would mislabel "we cannot tell" as a moderate mismatch and inflate \(w_{\text{tot}}\). The override pin to `Otherwise_related` is what guarantees the conservative outcome — two reports sharing a DOI for a multi-patient article are not flagged as duplicates when case labels can't be safely compared.

Advisory verdicts (`MULTI_PATIENT_CONTEXT_NO_LINK`, `NO_DECISIVE_SIGNAL`, `NOT_ASSESSED`) do **not** trigger the override — they fall through to the ordinary rarity-zone logic.

The chosen branch is recorded on `ClassificationResult.independence_reason` (`shared_source` for the override path, one of `rarity_strong`/`rarity_medium`/`rarity_weak`/`rarity_insufficient` for the zone paths) so downstream review can tell exactly which rule fired.

---

### Relationship between matching evidence and inconsistency tolerance

The zone-based design explicitly allows strong matching evidence to outweigh isolated inconsistencies. This reflects the known variability and occasional recording errors in pharmacovigilance data:

- In the strong evidence zone, a single strong inconsistency (such as mismatching patient sex) is tolerated because the combined rarity of the matched features makes coincidence unlikely.
- In the moderate evidence zone, only moderate inconsistencies are tolerated, and only a limited number.
- In the weak evidence zone, any inconsistency at all is enough to move the pair out of `Duplicates`.

Whenever a pair is moved out of the `Duplicates` zone in any of the three non-trivial zones, the rejection of independence still stands (matching evidence above `lmatch_weak` is treated as sufficient on its own), so the pair is surfaced as **Otherwise related**. Only below the weak threshold, where the matching evidence cannot reject independence at all, does the pair fall back to **Unrelated**.

---

### Parameter tuning

The following parameters define the decision behaviour and are configurable:

| Parameter | Default | Role |
|---|---|---|
| `lmatch_strong` | 3.0 | L\_match boundary for the strong evidence zone |
| `lmatch_med` | 2.0 | L\_match boundary for the moderate evidence zone |
| `lmatch_weak` | 1.0 | L\_match boundary for the weak evidence zone |
| `w_minor` | 0.4 | Weight per minor inconsistency in \(w_{\text{tot}}\) |
| `w_moderate` | 1.0 | Weight per moderate inconsistency in \(w_{\text{tot}}\) |
| `w_strong` | 3.0 | Weight per strong inconsistency in \(w_{\text{tot}}\) |
| `w_tot_strong_zone` | 4.0 | Maximum \(w_{\text{tot}}\) for `Duplicates` in the strong zone |
| `w_tot_med_zone` | 3.0 | Maximum \(w_{\text{tot}}\) for `Duplicates` in the moderate zone |
| `w_tot_weak_zone` | 1.0 | Maximum \(w_{\text{tot}}\) for `Duplicates` in the weak zone |

These parameters may be refined during method development using annotated report pairs to ensure appropriate calibration of the classification behaviour.

