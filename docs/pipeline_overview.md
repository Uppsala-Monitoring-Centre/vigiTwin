# Duplicate Adjudication Pipeline Overview

This document gives a high-level overview of the **duplicate adjudication pipeline** for pharmacovigilance reports. The pipeline operates on **report pairs** that have already been flagged as suspected duplicates by an upstream candidate-generation method (e.g. vigiMatch). For each pair it produces a final classification of **Duplicates**, **Otherwise related**, or **Unrelated**.

The pipeline is primarily a transparent rule-based decision logic operating on structured features, with optional LLM-assisted narrative analysis. It combines three complementary kinds of evidence:

1. **Rarity-based matching evidence**, which evaluates how unlikely it would be for two unrelated reports to share the observed combination of clinical features.
2. **Inconsistency signals** from structured data and clinical dates, which capture information arguing against the duplicate hypothesis.
3. **Narrative consistency** (planned), assessed using a language model to determine whether free-text narratives are clinically compatible with the same case.

The matching evidence is anchored in a **comparison population** (the *anchor population*) defined by the shared clinical context of the two reports. The method estimates the probability that **at least one** pair of reports anywhere in that population would share the observed feature pattern by chance, across all unordered pairs it contains; small probabilities produce strong evidence in favour of the duplicate hypothesis.

The combined evidence is then fed into a transparent decision logic that classifies each report pair into one of three user-facing categories:

- **Duplicates** — the pair very likely describes the same clinical case
- **Otherwise related** — different individuals or different events but meaningful clinical overlap remains (e.g. two patients in the same clinical study)
- **Unrelated** — the matching evidence is too weak to reject the independence null

> **Internal vs user-facing labels.** Internally the decision logic
> produces two layered labels: an **independence assessment**
> (`Non-independent / Independent`) driven by the matching evidence,
> plus, when independence is rejected, a **duplicate relationship**
> sub-label (`Duplicates / Otherwise related`) driven by the
> inconsistency signals. These are mapped to the user-facing labels
> above by `ClassificationResult.final_label`. The
> two-layer split exists because a small expected-collision count is
> evidence against independence but not specifically for the
> same-individual hypothesis (same reporter, same study, etc. can also
> produce strong matching evidence). New code should depend only on the
> user-facing labels — see `docs/duplicate_adjudication_spec.md`
> § Decision logic.

## Method overview

```mermaid
flowchart LR

A["Pharmacovigilance Reports"]

subgraph Data_Preparation["Offline Data Preparation"]
B["Structured Data"]
C["Narrative"]
D["Statistics<br>(drug sets, AE sets)"]
end

subgraph Adjudication["Duplicate Adjudication"]
E["Determine Reporting Context"]
F["Select Anchor Population<br>(drugs, AEs, drugs∩AEs)"]
H["Assess Matching Evidence"]
I["Assess Inconsistencies"]
J["Assess Narrative Consistency (LLM)"]
S["Extract Narrative Source/Case (LLM)<br>+ Deterministic Pair Comparison"]
K["Decision Logic<br>(rarity zones + shared-source override)"]
end

subgraph Output["Final classification"]
M["Duplicates"]
N["Otherwise related"]
O["Unrelated"]
end


A --> B
A --> C
A --> D

B --> E
B --> I
C --> B
C --> J
C --> S
D --> F
D --> H

E --> F
F --> H
J --> I
S --> I
S --> K


H --> K
I --> K
J --> K

K --> M
K --> N
K --> O
```

For full details of each component, see [`duplicate_adjudication_spec.md`](duplicate_adjudication_spec.md).
