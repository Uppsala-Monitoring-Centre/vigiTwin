# Integration Guide

This document explains how to run the same-individual assessment pipeline and what needs to be implemented in the secure repository (`llm_dup_pv_secure`) to connect it to real data.

---

## Architecture overview

```
llm_dup_dev (this repo)              llm_dup_pv_secure (secure repo)
─────────────────────────            ──────────────────────────────
Models, feature generation,          Database access, real data,
decision logic, eval framework       LLM execution via Azure

Defines:                             Implements:
  Report, ReportPair, PairFeatures     ReportLoader protocol
  FrequencyStats                       StatsProvider protocol
  PipelineRunner                       NarrativeProvider (future)
  ClassificationResult
  EvalSummary

        ← pip install llm_dup_dev ←
```

The secure repo installs `llm_dup_dev` as a dependency and provides concrete implementations of two protocols that connect the pipeline to the pharmacovigilance database.

---

## Prerequisites

### Install the package

In the secure repo's virtual environment:

```bash
pip install -e /path/to/llm_dup_dev[dev]
```

Or, if published to an internal package registry:

```bash
pip install llm-dup-dev[dev]
```

### Verify installation

```python
from llm_dup_dev.pipeline.runner import PipelineRunner
from llm_dup_dev.data.protocols import ReportLoader, StatsProvider
print("llm_dup_dev installed successfully")
```

---

## What you need to implement in the secure repo

### 1. ReportLoader

Loads structured `Report` objects from the database. Must implement three methods:

```python
from llm_dup_dev.data.protocols import ReportLoader
from llm_dup_dev.models.report import (
    Report, Drug, AdverseEvent, SourcedValue, AgeValue,
    MeasurementValue, DateValue, PartialDate,
)
from llm_dup_dev.models.report_pair import ReportPair


class SqlReportLoader:
    """Loads reports from SQL Server."""

    def __init__(self, connection_string: str):
        self._conn_str = connection_string

    def load_report(self, report_id: str) -> Report:
        """Query the database and build a Report object.

        Must populate:
        - patient_sex_values    (list of SourcedValue)
        - patient_age_values    (list of AgeValue)
        - patient_height_values (list of MeasurementValue)
        - patient_weight_values (list of MeasurementValue)
        - country_of_origin_values (list of SourcedValue)
        - report_date_values    (list of DateValue)
        - drugs                 (list of Drug, keyed by WHODrug substance)
        - adverse_events        (list of AdverseEvent, keyed by MedDRA PT)
        - narrative             (str or None)

        Use empty lists for missing/unknown attributes.
        Do NOT store uncertainty values (e.g. "unknown") as valid values.
        """
        # Your SQL queries here — adapt from legacy data_extraction.py
        ...

    def load_pair(self, pair: ReportPair) -> tuple[Report, Report]:
        return self.load_report(pair.report_1_id), self.load_report(pair.report_2_id)

    def load_pairs(self, pairs):
        for pair in pairs:
            r1, r2 = self.load_pair(pair)
            yield pair, r1, r2
```

**Key decisions when mapping from the database:**

| Attribute | Source | Notes |
|-----------|--------|-------|
| `patient_sex_values` | Structured field + LLM extraction | Use `sources=["structured"]` or `sources=["narrative_llm"]` |
| `patient_age_values` | Structured + LLM | Include `unit` ("years", "months", "days") |
| `patient_height_values` | Structured + LLM | Values in **centimetres (cm)** |
| `patient_weight_values` | Structured + LLM | Values in **kilograms (kg)** |
| `drugs[].substance` | WHODrug active substance name | Case-insensitive matching is handled downstream |
| `adverse_events[].pt_name` | MedDRA Preferred Term | Case-insensitive matching is handled downstream |
| `report_date_values` | Structured | Set `resolution` to "day", "month", or "year" based on available precision |
| `narrative` | Free-text narrative from the report | Used by narrative analysis (future); set to `None` if unavailable |

See `schemas/report.schema.json` for the full JSON schema and `examples/report_example.json` for a concrete example.

### 2. StatsProvider

Computes frequency statistics from the database for a given clinical context:

```python
from llm_dup_dev.data.protocols import StatsProvider
from llm_dup_dev.feature_generation.probability import FrequencyStats


class SqlStatsProvider:
    """Computes frequency statistics from the pharmacovigilance database."""

    def __init__(self, connection_string: str):
        self._conn_str = connection_string

    def compute_stats(
        self,
        matching_drugs: list[str],
        matching_AEs: list[str],
        country: str | None,
        year: int | None,
    ) -> FrequencyStats:
        """Run aggregate SQL queries to count:

        - N_total: total reports in the reporting context
        - N_drugs: reports containing ALL drugs in matching_drugs
        - N_AEs:   reports containing ALL AEs in matching_AEs
        - N_drugs_AEs: reports containing both

        The reporting context is determined by country and year:
        - country + year → filter by both
        - country only   → filter by country
        - both None       → global (all reports)
        """
        # Your SQL aggregate queries here
        ...
        return FrequencyStats(
            N_total=n_total,
            N_drugs=n_drugs,
            N_AEs=n_aes,
            N_drugs_AEs=n_drugs_aes,
        )
```

**SQL query guidance:**

For `N_drugs` (reports containing ALL matching drugs):
```sql
-- For matching_drugs = ['pregabalin', 'sertraline']
SELECT COUNT(DISTINCT report_id)
FROM drug_table
WHERE substance IN ('pregabalin', 'sertraline')
  AND <context_filter>
GROUP BY report_id
HAVING COUNT(DISTINCT substance) = 2  -- must contain ALL drugs
```

Apply the same pattern for `N_AEs` and `N_drugs_AEs` (intersection).

---

## Running the pipeline

### Single pair

```python
from llm_dup_dev.pipeline.runner import PipelineRunner
from llm_dup_dev.models.report_pair import ReportPair

# Set up with your implementations
runner = PipelineRunner(
    report_loader=SqlReportLoader(conn_str),
    stats_provider=SqlStatsProvider(conn_str),
)

# Process a single pair
pair = ReportPair(
    pair_id="12345__67890",
    report_1_id="12345",
    report_2_id="67890",
)
result = runner.run_pair(pair)

print(result.final_label)          # "Duplicates", "Otherwise_related", "Unrelated"
print(result.features.L_match)     # e.g. 4.2
print(result.classification.explanation)  # human-readable reasoning
```

### Batch processing

```python
pairs = [
    ReportPair(pair_id=f"{a}__{b}", report_1_id=a, report_2_id=b)
    for a, b in candidate_pairs  # from vigiMatch or similar
]

batch = runner.run_batch(
    pairs,
    continue_on_error=True,  # skip failures, log warnings
)

print(f"Processed: {batch.n_processed}, Errors: {batch.n_errors}")

# Get labels for all processed pairs
labels = batch.labels()  # {"12345__67890": "Duplicates", ...}
```

### With narrative consistency (future)

When the narrative analysis module is implemented:

```python
from llm_dup_dev.models.enums import NarrativeConsistency

def my_narrative_provider(pair_id, narrative_1, narrative_2):
    """Call Azure OpenAI to assess narrative consistency."""
    # Your LLM call here
    return NarrativeConsistency.CONSISTENT

batch = runner.run_batch(pairs, narrative_provider=my_narrative_provider)
```

Until then, the pipeline defaults to `NarrativeConsistency.NEUTRAL`, which has no effect on the classification.

---

## Running evaluation

### Against human annotations

```python
from llm_dup_dev.eval import evaluate_batch, summary_to_text

# annotations: list of dicts from the eval set
# Each must have: pair_id, duplicates_assessment
annotations = load_eval_annotations()  # your loading function

summary = evaluate_batch(
    batch,
    annotations,
    experiment_id="v1_baseline",
    config_snapshot={"thresholds": "default", "note": "initial run"},
)

print(summary_to_text(summary))
```

**Expected output:**
```
=== Experiment: v1_baseline ===
Pairs evaluated: 138  (abstained: 12)

--- Final Classification ---
  Accuracy:                    0.823
  Duplicates              P=0.880  R=0.850  F1=0.865
  Otherwise_related       P=0.720  R=0.690  F1=0.705
  Unrelated               P=0.810  R=0.840  F1=0.825
```

### Eval annotation format

Each annotation dict must contain:

| Field | Type | Values |
|-------|------|--------|
| `pair_id` | str | Must match pipeline output |
| `duplicates_assessment` | str | "duplicates", "otherwise_related", "unrelated", "abstain" |

The pipeline maps its internal labels to match:

| Pipeline output | Annotation value |
|-----------------|------------------|
| Same individual | `duplicates` |
| Different / Related | `otherwise_related` |
| Different / Unrelated | `unrelated` |

---

## Configuration

### Pipeline configuration

```python
from llm_dup_dev.classifier.config import (
    PipelineConfig, DecisionThresholds,
    DomainSizeConfig, InconsistencyThresholds, AnchorConfig,
)

# All parameters have sensible defaults — only override what you need
config = PipelineConfig(
    domain_sizes=DomainSizeConfig(
        age_years_range=101,     # 0–100 years
        sex_categories=2,
        height_cm_range=50,      # ~150–200 cm
        weight_kg_range=100,     # ~40–140 kg
        date_plausible_years=10, # treatment history span
    ),
    inconsistency_thresholds=InconsistencyThresholds(
        age_moderate=3.0,   age_strong=10.0,
        height_moderate=5.0, height_strong=10.0,
        weight_moderate=5.0, weight_strong=15.0,
    ),
    anchor=AnchorConfig(threshold=50),
    epsilon=1e-15,
)

thresholds = DecisionThresholds(
    lmatch_strong=3.0,   # strong evidence zone
    lmatch_med=2.0,      # moderate evidence zone
    lmatch_weak=1.0,     # weak evidence zone
    w_moderate=1.0,      # weight for moderate inconsistencies
    w_strong=3.0,        # weight for strong inconsistencies
    w_tot_strong_zone=4.0,  # w_tot threshold for strong zone
    w_tot_med_zone=3.0,     # w_tot threshold for medium zone
)

runner = PipelineRunner(
    report_loader=loader,
    stats_provider=stats_provider,
    pipeline_config=config,
    decision_thresholds=thresholds,
)
```

### Decision logic summary

Inconsistencies are combined into a single weighted score:

    w_tot = w_moderate × moderate + w_strong × strong  (defaults: 1 × moderate + 3 × strong)

| L_match zone | Same individual if... | Otherwise → |
|---|---|---|
| > 3.0 (strong) | `w_tot < 4` | Different / Related |
| > 2.0 (medium) | `w_tot < 3` | Different / Related |
| > 1.0 (weak)   | `w_tot == 0` | Different / Unrelated |
| ≤ 1.0          | never | Different / Unrelated |

Decisive inconsistencies (e.g. distinct patient IDs from the same study) always force Different, with Related/Unrelated determined by whether L_match > lmatch_med.

---

## Reference files

| File | Purpose |
|------|---------|
| `schemas/*.schema.json` | JSON schemas generated from Pydantic models |
| `examples/*_example.json` | Concrete example objects |
| `docs/same_individual_assessment_spec.md` | Full method specification |
| `docs/data_contracts.md` | Data layer design and contracts |
| `docs/repository_roles.md` | Security controls between repos |

---

## Testing

### Run the test suite (in llm_dup_dev)

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
```

182 tests covering all modules.

### Quick smoke test (in the secure repo)

```python
from llm_dup_dev.data.synthetic import SyntheticReportLoader, SyntheticStatsProvider
from llm_dup_dev.pipeline.runner import PipelineRunner
from llm_dup_dev.models.report import Report, Drug, AdverseEvent, SourcedValue, AgeValue
from llm_dup_dev.models.report_pair import ReportPair

# Build two minimal test reports
r1 = Report(
    report_id="TEST_1",
    drugs=[Drug(substance="aspirin", sources=["structured"])],
    adverse_events=[AdverseEvent(pt_name="headache", sources=["structured"])],
    patient_age_values=[AgeValue(value=45, sources=["structured"])],
    patient_sex_values=[SourcedValue(value="male", sources=["structured"])],
)
r2 = Report(
    report_id="TEST_2",
    drugs=[Drug(substance="aspirin", sources=["structured"])],
    adverse_events=[AdverseEvent(pt_name="headache", sources=["structured"])],
    patient_age_values=[AgeValue(value=45, sources=["structured"])],
    patient_sex_values=[SourcedValue(value="male", sources=["structured"])],
)

loader = SyntheticReportLoader({"TEST_1": r1, "TEST_2": r2})
stats = SyntheticStatsProvider(default_N_total=5000, default_N_drugs=30,
                                default_N_AEs=50, default_N_drugs_AEs=10)
runner = PipelineRunner(report_loader=loader, stats_provider=stats)

pair = ReportPair(pair_id="TEST_1__TEST_2", report_1_id="TEST_1", report_2_id="TEST_2")
result = runner.run_pair(pair)

print(f"L_match: {result.features.L_match:.2f}")
print(f"Label:   {result.final_label}")
print(f"Reason:  {result.classification.explanation}")
```

---

## What's not yet implemented

| Component | Status | Notes |
|-----------|--------|-------|
| Narrative analysis (Phase 9) | Deferred | LLM-based narrative consistency assessment. The pipeline currently defaults to NEUTRAL. When implemented, the `NarrativeProvider` callback will supply narrative-derived inconsistencies (moderate, strong, decisive) that feed into the existing decision logic. |
