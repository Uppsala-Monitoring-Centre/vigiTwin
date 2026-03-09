# Data Structure Overview

## Purpose

This document defines the **data contracts** used in the LLM-assisted same-individual assessment pipeline.

The purpose of these contracts is to ensure that:

- each stage of the pipeline has a **clear and stable interface**
- data structures are **explicit and reproducible**
- feature engineering can evolve **without breaking upstream components**
- the method remains **transparent and auditable**

The pipeline processes information about pharmacovigilance reports in **three conceptual layers**.

| Layer        | Purpose                                          | Stability   |
| ------------ | ------------------------------------------------ | ----------- |
| Report       | Structured representation of a single report     | Very stable |
| ReportPair   | Relationship between two reports being evaluated | Stable      |
| PairFeatures | Derived features used for duplicate assessment   | May evolve  |

Each layer builds on the previous one.

------

## Layer Relationships

The data flows through the system as follows:

```
Raw reports
     ↓
Report objects
     ↓
ReportPair objects
     ↓
PairFeatures objects
     ↓
Duplicate decision logic
```

This layered design separates **data extraction**, **pair construction**, and **feature generation**, allowing the analytical components of the method to evolve without modifying the core report representation.

------

## Report Layer

The **Report layer** contains the structured representation of a single pharmacovigilance report.

This representation includes:

- structured database fields
- attributes extracted from narratives using LLMs
- sets of drugs and adverse events

The Report layer is designed to be **stable over time**, since it reflects the information available in the source data rather than derived analytical features.

This layer is the **primary input** for all subsequent stages of the pipeline.

------

## ReportPair Layer

The **ReportPair layer** represents a pair of reports that are being evaluated for possible duplication.

Each ReportPair object contains:

- identifiers of the two reports
- contextual information used for probability estimation (e.g. reporting context)
- sets of matching clinical features such as:

```
matching_drugs
matching_AEs
```

These sets define the **shared clinical context** of the two reports.

Within the duplicate assessment framework, these shared feature sets are used to determine the **anchor population**, which defines the population within which collision probabilities are evaluated same_individual_assessment_desi….

The ReportPair layer is expected to remain **relatively stable**, although minor additions may occur as the pair comparison logic evolves.

------

## PairFeatures Layer

The **PairFeatures layer** contains the derived features used by the duplicate assessment method.

These features are computed from the Report and ReportPair layers and may include:

- anchor population statistics
- feature-level probabilities
- composite matching evidence
- detected inconsistencies
- narrative similarity assessments

Examples include:

```
L_match
N_anchor
inconsistencies
narrative_consistency
```

These quantities form the inputs to the final decision logic that classifies report pairs as:

```
Same individual
Different individuals
Abstain
```

Because these features are part of the analytical framework, the PairFeatures layer may **evolve during method development**. Versioning of feature definitions may therefore be required to ensure reproducibility.

------

## Design Principles

The data contracts follow several guiding principles.

### Separation of concerns

Each layer has a clearly defined responsibility:

- **Report:** represent raw extracted information
- **ReportPair:** represent relationships between reports
- **PairFeatures:** represent analytical evidence

------

### Stability of upstream layers

The **Report layer should change as little as possible**, since it reflects the source data.

Most methodological evolution should occur in the **PairFeatures layer**.

------

### Explicit representation of uncertainty

Where conflicting or uncertain values exist (e.g. structured versus narrative extraction), the schema preserves **multiple candidate values** rather than resolving conflicts during data extraction.

This avoids premature information loss and allows downstream methods to evaluate the evidence explicitly.

------

### Support for probabilistic reasoning

The schema is designed to support the probabilistic matching framework described in the method specification, in which the evidential value of matching features depends on the size of the corresponding anchor population and the joint probability of observing the matched feature set by chance same_individual_assessment_desi….

------

The following section describes the **Report layer schema** in detail.



# Report Layer Schema

## Purpose

The **Report layer** represents structured information extracted from a single pharmacovigilance report.

This layer combines:

1. **Structured database fields**
2. **LLM-extracted attributes derived from narratives**

The schema preserves **multiple candidate values per attribute** when conflicting information is present.
 Each value records its **source** to support auditing and debugging.

Possible sources currently supported:

```
structured
narrative_llm
```

Values representing **missing or uncertain information must not be stored as valid values**.

Matching between reports uses a **set intersection rule**:
$$
\text{match if } intersection(values_1, values_2) \neq \varnothing
$$

------

# Report Object

Each report is represented as a structured object.

Example:

```
{
  "report_id": "17887519-1",

  "patient_sex_values": [...],
  "patient_age_values": [...],
  "country_of_origin_values": [...],
  "report_date_values": [...],

  "drugs": [...],
  "adverse_events": [...]
}
```

------

# Demographic Attributes

## Patient Sex

```
patient_sex_values: [
  {
    "value": "female",
    "sources": ["structured"]
  }
]
```

Possible values are limited to valid sex categories recorded in the source system.

Values representing uncertainty (e.g. `"unknown"`, `"uncertain"`) must not be stored.

------

## Patient Age

Age values may originate from structured fields or narrative extraction.

```
patient_age_values: [
  {
    "value": 54,
    "unit": "years",
    "sources": ["structured"]
  },
  {
    "value": 55,
    "unit": "years",
    "sources": ["narrative_llm"]
  }
]
```

Multiple values may exist when sources disagree.

------

## Country of Origin

```
country_of_origin_values: [
  {
    "value": "US",
    "sources": ["structured"]
  }
]
```

Country codes should follow the standard used in the source database.

------

## Report Date

Report date represents the date the report was received or recorded.

```
report_date_values: [
  {
    "value": "2020-06-12",
    "resolution": "day",
    "sources": ["structured"]
  }
]
```

Resolution may be one of:

```
day
month
year
```

This resolution information may later be used when estimating domain sizes for probability calculations.

------

## Drug Information

Drug information is represented as a **set of drug objects**, each corresponding to a **WHODrug active substance name**.

Each drug may optionally include additional contextual information.

```
drugs: [
  {
    "substance": "pregabalin",

    "sources": ["structured"],

    "form": "capsule",
    "strength": "75 mg",

    "start_date": {
      "value": "2020-05-01",
      "resolution": "day"
    },

    "end_date": {
      "value": "2020-06-01",
      "resolution": "day"
    }
  }
]
```

### Notes

- `substance` should correspond to the **WHODrug active substance name**
- additional attributes (`form`, `strength`, dates) are optional
- drugs should be interpreted as **sets** when comparing reports

Matching drugs between two reports are determined using the **substance name**.

------

## Adverse Events

Adverse events are represented as a **set of MedDRA Preferred Terms (PTs)**.

Each event may optionally include onset and resolution dates.

```
adverse_events: [
  {
    "pt_name": "somnolence",

    "sources": ["structured"],

    "start_date": {
      "value": "2020-05-15",
      "resolution": "day"
    },

    "end_date": {
      "value": "2020-05-20",
      "resolution": "day"
    }
  }
]
```

### Notes

- `pt_name` should correspond to a **MedDRA Preferred Term**
- adverse events are compared using the **PT name**
- date fields are optional

------

## Representation Principles

### Set Semantics

The following attributes should be interpreted as **sets**:

```
drugs
adverse_events
```

This enables detection of:

```
matching_drugs
matching_AEs
```

which form the basis for **anchor population selection** in the probabilistic framework same_individual_assessment_desi….

------

### Multiple Values

Multiple candidate values may occur when:

- structured and narrative sources disagree
- narrative extraction yields multiple interpretations

Example:

```
patient_age_values = [
  {"value": 54, "sources": ["structured"]},
  {"value": 55, "sources": ["narrative_llm"]}
]
```

Such conflicts are preserved rather than resolved during extraction.

------

### Missing Values

Attributes with no reliable value should be represented as:

```
[]
```

Example:

```
patient_sex_values: []
```

Missing values must not produce matching evidence.

------

## Relationship to Downstream Processing

The Report layer provides the inputs used to derive:

```
matching_drugs
matching_AEs
```

which are used to determine the **anchor population** and estimate matching probabilities in the duplicate detection framework same_individual_assessment_desi….

Additional attributes such as demographics and dates contribute **additional matching evidence** and may also generate **inconsistency signals** during pair evaluation.