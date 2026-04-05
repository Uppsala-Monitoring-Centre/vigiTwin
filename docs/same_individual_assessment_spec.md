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

- **Drug set** — when not part of the anchor definition (leave-pair-out Bayesian-smoothed frequency)
- **AE set** — when not part of the anchor definition (leave-pair-out Bayesian-smoothed frequency)
- **Patient demographics** — age, sex, height, weight (domain-size based)
- **Clinical dates** — unique dates matching across drug start/end and AE onset fields (domain-size based)

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

For date features, domain size depends on the recorded resolution, the reporting context, and the temporal span of matching dates across the report pair.

###### Context-dependent defaults

The default temporal window depends on the reporting context. When the reporting context is **country-year**, a narrower 1-year window is used because most reports in a given country-year are temporally concentrated. For broader contexts (country or global), a 10-year window is used — although the database spans nearly 50 years, exponential growth means most reports have been submitted in the most recent decade.

| Reporting context | Default window | Exact date \(D_f\) | Month–year \(D_f\) | Year only \(D_f\) |
| ----------------- | -------------- | ------------------- | ------------------- | ------------------ |
| Country-year      | 1 year         | 365                 | 12                  | 1                  |
| Country or global | 10 years       | 3650                | 122                 | 10                 |

###### Span-based extension

If the matching dates across the two reports span a period longer than the default window, the domain size is extended to cover the actual span. This prevents underestimating the domain when reports contain dates spread across a wide temporal range.

For example, if matched drug start dates include 2001-02-01 and matched AE onset dates include 2007-03-03, the span is approximately 6 years. In a country-year context (default 365 days), the domain would be extended to the actual span (~2,226 days).

The span is computed from all matched date values (including clinical dates and report dates) and applies uniformly to all date-type features in the probability computation.

---

##### Age

Age domain size depends on the resolution of the recorded age.

| Representation    | Example  | Domain size                                           |
| ----------------- | -------- | ----------------------------------------------------- |
| Age in years      | 54       | number of plausible ages (e.g. 0–100 → \(D_f = 101\)) |
| Age in months     | 7 months | plausible months within infancy                       |
| Age with decimals | 54.2     | span of ages × decimal resolution                     |

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

For height, the domain size depends on the plausible height span for the patient’s age group and the measurement resolution.

Examples:

| Context | Span       | Resolution | Domain size         |
| ------- | ---------- | ---------- | ------------------- |
| Adult   | 150–200 cm | 1 cm       | \(D_f \approx 50\)  |
| Child   | 80–100 cm  | 1 cm       | \(D_f \approx 20\)  |
| Adult   | 150–200 cm | 0.1 cm     | \(D_f \approx 500\) |

Larger plausible spans produce smaller random-match probabilities and therefore stronger evidence for exact matches.

---

##### Weight

Weight is handled analogously to height.

Examples:

| Context | Span      | Resolution | Domain size         |
| ------- | --------- | ---------- | ------------------- |
| Adult   | 40–140 kg | 1 kg       | \(D_f \approx 100\) |
| Child   | 5–25 kg   | 1 kg       | \(D_f \approx 20\)  |
| Child   | 5–25 kg   | 0.1 kg     | \(D_f \approx 200\) |

Higher measurement precision increases domain size and therefore increases the evidential value of an exact match.

---

##### Drug Sets and Adverse Event Sets

For drug sets and adverse event sets, the probability is estimated using a **leave-pair-out Bayesian-smoothed** conditional frequency within the anchor population. This avoids self-inclusion bias from the two focal reports and prevents zero-probability pathologies for rare combinations.

Drug sets and AE sets are evaluated only when they are **not part of the anchor definition**, to avoid double-counting anchor information.

###### Leave-Pair-Out Estimation

When evaluating an additional matching feature \(F\) (e.g. the drug set) within an anchor population \(A\) (e.g. the AE set), for a candidate pair where both reports match on \(F\):

1. **Remove the focal pair** from the anchor counts:

$$
n_{\text{other}} = \max(0,\; n_{\text{anchor}} - 2)
$$
$$
k_{\text{other}} = \max(0,\; k_{\text{anchor+feature}} - 2)
$$

where \(n_{\text{anchor}}\) is the number of reports in the relevant context matching the anchor, and \(k_{\text{anchor+feature}}\) is the number of those also matching the additional feature.

2. **Compute the prior** from the broader context (without conditioning on the anchor):

$$
p_0 = \frac{N_{\text{feature in context}}}{N_{\text{total in context}}}
$$

For example, if the anchor is the AE set and the additional feature is the drug set, then \(p_0\) is the prevalence of the drug set in the relevant context (country/year or global), **not** among only reports with those AEs.

3. **Apply Bayesian smoothing**:

$$
\hat{p}(F \mid A, \text{context}) = \frac{k_{\text{other}} + m \cdot p_0}{n_{\text{other}} + m}
$$

where \(m = 10\) is a smoothing pseudo-count.

###### Design Rationale

- **Leave-pair-out** prevents the focal pair from inflating its own evidence. Without this correction, the two reports being evaluated contribute to the count that is supposed to measure how common their shared feature is among *other* reports.
- **Bayesian smoothing** prevents zero probabilities when the leave-pair-out count is zero (i.e. no other reports in the anchor share the feature), which would otherwise cause undefined log-probability values. The prior \(p_0\) pulls the estimate toward the marginal prevalence.
- **\(m = 10\)** provides moderate regularisation: with 23 or more leave-pair-out reports, the data dominates; with very few, the prior dominates.

###### Worked Example

Suppose:
- Anchor = matching AE set, \(n_{\text{anchor}} = 25\)
- Additional feature = matching drug set, \(k_{\text{anchor+feature}} = 3\) (3 of the 25 anchor reports also have the matching drugs)
- The focal pair are among those 3
- Drug set prevalence in context: \(N_{\text{drugs}} = 200\), \(N_{\text{total}} = 10{,}000\)

Then:

$$
n_{\text{other}} = 25 - 2 = 23, \quad k_{\text{other}} = 3 - 2 = 1
$$
$$
p_0 = \frac{200}{10{,}000} = 0.02
$$
$$
\hat{p} = \frac{1 + 10 \times 0.02}{23 + 10} = \frac{1.2}{33} \approx 0.0364
$$

This logic applies symmetrically: if the anchor is the drug set and the additional feature is the AE set, the same formula is used with the AE set's marginal prevalence as \(p_0\).

---

##### Clinical Dates (Drug Start/End, AE Onset)

Matching dates from drug and adverse event records provide additional evidence for the collision probability. This uses **explicit date-field coincidence** only — treatment intervals are NOT expanded into sets of intermediate dates.

###### Which dates are compared

For each matched drug (shared between the two reports), the following date fields are compared:

- **start date** ↔ **start date**
- **end date** ↔ **end date**

For each matched adverse event, the following field is compared:

- **onset date** ↔ **onset date**

Only same-field-type comparisons count. A drug's start date on one report must NOT match against the same drug's end date on the other report. Dates from unmatched drugs or unmatched AEs are not considered.

Standard partial-date matching rules apply: month-only and year-only values match at their coarser resolution.

###### Deduplication to unique dates

The result is collapsed to a set of **unique matched dates**. If the same calendar date matches for multiple drugs, multiple AEs, or multiple field types, it contributes only once (at the finest matched resolution).

Each unique matched date contributes \(p_f = 1/D_f\) to the composite probability, where \(D_f\) is the context-dependent date domain size.

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
| decisive | hard contradiction that overrides all matching evidence |

Moderate and strong inconsistencies are derived from structured data comparisons (age, sex, height, weight, drug set). Their thresholds are based on typical variability and recording errors in pharmacovigilance data. The current thresholds specified below are starting values and may need refinement. It's possible that they should be adaptive (e.g. account for patient age when assessing mismatching height and weight). Since recording errors or misclassification are known to occur for any fields on adverse event reports, strong mismatches are not considered decisive on their own and must be interpreted together with the strength of the matching evidence via the weighted inconsistency score (see Decision logic below).

Decisive inconsistencies are expected to originate from narrative analysis (e.g. distinct patient identifiers mentioned in case narratives from the same study). When present, they override all matching evidence and force a Different individuals classification.

------

#### Structured Data Inconsistencies

##### Age

Age differences are evaluated using the absolute difference in years.

| Difference | Classification         |
| ---------- | ---------------------- |
| ≥ 3 years  | moderate inconsistency |
| ≥ 10 years | strong inconsistency   |

------

##### Height

Height differences are evaluated in centimetres.

| Difference | Classification         |
| ---------- | ---------------------- |
| ≥ 5 cm     | moderate inconsistency |
| ≥ 10 cm    | strong inconsistency   |

------

##### Weight

Weight differences are evaluated in kilograms.

| Difference | Classification         |
| ---------- | ---------------------- |
| ≥ 5 kg     | moderate inconsistency |
| ≥ 15 kg    | strong inconsistency   |

------

##### Sex

A mismatch in recorded patient sex is treated as a strong inconsistency:

```
sex_R1 ≠ sex_R2
```

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
- Only drug **presence** is evaluated; differences in dosage, form, or dates are not considered here — date differences are handled by the Drug Dates check below.

-------

##### Drug Dates

Drug date inconsistencies detect timing mismatches for drugs that appear in **both** reports. Only shared drugs are evaluated.

**Partial date intervals**

Each `PartialDate` is converted to a date interval based on its resolution:

| Resolution | Interval |
| ---------- | -------- |
| day | [date, date] |
| month | [YYYY-MM-01, last day of month] |
| year | [YYYY-01-01, YYYY-12-31] |

**Interval distance**

The minimum distance between two intervals is:
```
if overlap: diff_days = 0
else:       diff_days = min(|start1 − end2|, |start2 − end1|)
```

**Weighting**

| Difference | Weight |
| ---------- | ------ |
| 0 days (exact / overlapping) | 0.0 |
| 1–7 days | 0.5 |
| 8–30 days | 1.0 |
| > 30 days | 3.0 |

If the substance is concomitant in **both** reports, the weight is multiplied by 0.5 (lower reliability of timing for concomitant drugs).

**Per-drug score**

For each shared drug, start and stop dates are compared independently (if both reports have at least one value for that date type). Multiple dates per report are handled by taking the minimum pairwise interval distance. The per-drug contribution is capped at 3.0.

**Classification**

| Total score | Result |
| ----------- | ------ |
| ≥ 3.0 | strong inconsistency |
| ≥ 1.0 | moderate inconsistency |
| < 1.0 | no inconsistency |

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

The final classification of a report pair is determined by jointly evaluating:

- the strength of the **rarity-based matching evidence** (\(L_{\text{match}}\)),
- the presence and severity of **detected inconsistencies**, and
- the **narrative consistency assessment**.

The decision logic balances positive evidence suggesting that the reports describe the same individual against contradictory evidence suggesting that they describe different individuals.

The decision logic produces one of three primary outcomes:

- **Same individual**
- **Different individuals**
- **Abstain**

If the classification is **Different individuals**, the method additionally assesses whether the reports appear to be clinically related:

- **Related**
- **Unrelated**
- **Abstain**

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
w_{\text{tot}} = w_m \times n_{\text{moderate}} + w_s \times n_{\text{strong}}
$$

which combines moderate and strong inconsistencies into a single score. The weights \(w_m\) and \(w_s\) are configurable (defaults: \(w_m = 1\), \(w_s = 3\)).

**Decisive inconsistency count**

The number of decisive inconsistencies detected (typically from narrative analysis). Any non-zero count overrides the zone-based logic.

**Narrative consistency category**

One of:

- strongly consistent
- consistent
- neutral
- inconsistent

---

### Weighted inconsistency score

Moderate and strong inconsistencies are combined into a single weighted score:

$$
w_{\text{tot}} = w_m \times n_{\text{moderate}} + w_s \times n_{\text{strong}}
$$

where \(w_m\) and \(w_s\) are configurable weights. With the current defaults (\(w_m = 1\), \(w_s = 3\)), a single strong inconsistency contributes three times as much as a moderate one.

---

### Decision zones

The decision space is divided into four zones based on the magnitude of \(L_{\text{match}}\). Each zone applies a progressively stricter tolerance for \(w_{\text{tot}}\):

| Zone | Condition | Same individual if... | Otherwise |
|---|---|---|---|
| Strong evidence | \(L_{\text{match}} > 3.0\) | \(w_{\text{tot}} < 4\) | Different / Related |
| Moderate evidence | \(L_{\text{match}} > 2.0\) | \(w_{\text{tot}} < 3\) | Different / Related |
| Weak evidence | \(L_{\text{match}} > 1.0\) | \(w_{\text{tot}} < 1\) | Different / Unrelated |
| Below threshold | \(L_{\text{match}} \leq 1.0\) | never | Different / Unrelated |

The L\_match zone boundaries (3.0, 2.0, 1.0), the zone-specific \(w_{\text{tot}}\) thresholds (4, 3), and the inconsistency weights (\(w_m\), \(w_s\)) are all configurable parameters.

**Examples** (using default parameters):

- \(L_{\text{match}} = 4.2\), one strong inconsistency: \(w_{\text{tot}} = 3 < 4\) → **Same individual**
- \(L_{\text{match}} = 4.2\), one strong + one moderate: \(w_{\text{tot}} = 4\), not \(< 4\) → **Different / Related**
- \(L_{\text{match}} = 2.5\), two moderate inconsistencies: \(w_{\text{tot}} = 2 < 3\) → **Same individual**
- \(L_{\text{match}} = 2.5\), one strong inconsistency: \(w_{\text{tot}} = 3\), not \(< 3\) → **Different / Related**
- \(L_{\text{match}} = 1.5\), no inconsistencies: \(w_{\text{tot}} = 0\) → **Same individual**
- \(L_{\text{match}} = 1.5\), one moderate inconsistency: \(w_{\text{tot}} = 1\), not \(< 1\) → **Different / Unrelated**
- \(L_{\text{match}} = 0.8\): below threshold → **Different / Unrelated** regardless of inconsistencies

---

### Decisive inconsistencies

Decisive inconsistencies (e.g. distinct patient identifiers from the same study, identified through narrative analysis) override all matching evidence and force a **Different individuals** classification.

When a decisive inconsistency is present, the relationship sub-classification is determined solely by whether the reports share meaningful clinical context:

- \(L_{\text{match}} > 2.0\) → **Related**
- \(L_{\text{match}} \leq 2.0\) → **Unrelated**

---

### Relationship between matching evidence and inconsistency tolerance

The zone-based design explicitly allows strong matching evidence to outweigh isolated inconsistencies. This reflects the known variability and occasional recording errors in pharmacovigilance data:

- In the strong evidence zone, a single strong inconsistency (such as mismatching patient sex) is tolerated because the combined rarity of the matched features makes coincidence unlikely.
- In the moderate evidence zone, only moderate inconsistencies are tolerated, and only a limited number.
- In the weak evidence zone, any inconsistency tips the balance towards different individuals.

When a pair is classified as **Different individuals** in the strong or moderate evidence zones, the reports are labelled **Related** because significant clinical overlap remains. In the weak evidence and below-threshold zones, they are labelled **Unrelated**.

---

### Parameter tuning

The following parameters define the decision behaviour and are configurable:

| Parameter | Default | Role |
|---|---|---|
| `lmatch_strong` | 3.0 | L\_match boundary for the strong evidence zone |
| `lmatch_med` | 2.0 | L\_match boundary for the moderate evidence zone |
| `lmatch_weak` | 1.0 | L\_match boundary for the weak evidence zone |
| `w_moderate` | 1.0 | Weight per moderate inconsistency in \(w_{\text{tot}}\) |
| `w_strong` | 3.0 | Weight per strong inconsistency in \(w_{\text{tot}}\) |
| `w_tot_strong_zone` | 4.0 | Maximum \(w_{\text{tot}}\) for Same in the strong zone |
| `w_tot_med_zone` | 3.0 | Maximum \(w_{\text{tot}}\) for Same in the moderate zone |
| `w_tot_weak_zone` | 1.0 | Maximum \(w_{\text{tot}}\) for Same in the weak zone |

These parameters may be refined during method development using annotated report pairs to ensure appropriate calibration of the classification behaviour.

