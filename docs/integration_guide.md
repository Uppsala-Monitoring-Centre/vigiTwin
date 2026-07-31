# Integration Guide

This document explains how to run the **duplicate adjudication pipeline** (the report-pair classification pipeline implemented in this repo) and what needs to be implemented in the secure repository (`llm_dup_pv_secure`) to connect it to real data.

The pipeline takes candidate report pairs (typically produced upstream by vigiMatch), computes structured pair features, and produces a final classification of `Duplicates`, `Otherwise_related`, `Unrelated`, or `Abstain`. See [`pipeline_overview.md`](pipeline_overview.md) for the high-level description and [`duplicate_adjudication_spec.md`](duplicate_adjudication_spec.md) for the full method specification.

---

## Architecture overview

```
llm_dup_dev (this repo)              llm_dup_pv_secure (secure repo)
─────────────────────────            ──────────────────────────────
Models, feature generation,          Database access, real data,
decision logic, eval framework       LLM execution via Azure

Defines:                             Implements:
  Report, ReportPair, PairFeatures     ReportLoader protocol
  FrequencyStats                       StatsProvider protocol (live or offline)
  PipelineRunner
  ClassificationResult
  EvalSummary

        ← pip install llm_dup_dev ←
```

The secure repo installs `llm_dup_dev` as a dependency and provides concrete implementations of two protocols that connect the pipeline to the pharmacovigilance database.

The pipeline supports **two operating modes**:

| Mode | When to use | StatsProvider implementation |
|------|-------------|------------------------------|
| **Live** | Production / online evaluation | `SqlStatsProvider` — queries the database at runtime |
| **Offline (frozen validation)** | Evaluation batches without DB access | `FrozenStatsProvider` — reads precomputed per-pair bundles |

Both modes use the same `PipelineRunner` API. The only difference is which `StatsProvider` you pass in.

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

---

## Offline frozen-validation mode

### Why it exists

Frozen validation datasets are used to run evaluation batches in environments where no live database connection is available (e.g. locked-down compute clusters). The secure repo's preprocessing step computes all necessary frequency statistics up front and stores them alongside the reports. At evaluation time the pipeline reads these precomputed counts instead of querying the database.

### Frozen pair-frequency artifact

The preprocessing step produces one `FrozenPairBundle` per pair. Each bundle contains:

```
FrozenPairBundle
  pair_id               — matches the ReportPair.pair_id used at eval time
  matching_drugs        — canonical sorted list of shared drug substances
  matching_AEs          — canonical sorted list of shared AE preferred terms
  global_stats          — frequency counts for the global context (always present)
  country_stats         — counts for the country context (present if reports share country)
  country_year_stats    — counts for the country+year context (present if reports share both)
```

Each scope payload (`FrozenScopeStats`) contains `N_total`, `N_drugs`, `N_AEs`, `N_drugs_AEs` — the same four counts the live `StatsProvider` returns.

### Scope rules

These mirror the pipeline's `determine_reporting_context` logic:

| Reports share... | Scopes to store |
|-----------------|-----------------|
| country + year  | global + country + country_year |
| country only    | global + country |
| neither         | global only |

### How the pipeline uses it

The pipeline still computes matching and reporting context as usual (steps 1–3 in `run_pair`). The `FrozenStatsProvider` intercepts step 4:

1. `PipelineRunner` detects that the provider implements `PairContextSink` and calls `set_current_pair(pair.pair_id)`.
2. `compute_stats(matching_drugs, matching_AEs, country, year)` is called as normal.
3. The provider looks up the bundle by `pair_id`, verifies the recomputed matches against the frozen values, then returns the precomputed counts for the appropriate scope.

No database query occurs.

### Validation checks

`FrozenStatsProvider` fails loudly if:

- `set_current_pair` was not called (`RuntimeError`) — indicates the provider was used outside `PipelineRunner`.
- No bundle exists for the current `pair_id` (`KeyError`) — indicates the preprocessing step missed this pair.
- The requested scope is not in the bundle (`KeyError`) — indicates a scope rule mismatch between preprocessing and the pipeline.
- The pipeline's recomputed `matching_drugs` or `matching_AEs` differ from the frozen values (`ValueError`) — indicates pipeline version drift since the bundles were generated.

### Setting up offline mode in the secure repo

```python
from llm_dup_dev.data.offline import (
    FrozenPairBundle,
    FrozenScopeStats,
    FrozenStatsProvider,
)
from llm_dup_dev.pipeline.runner import PipelineRunner

# Build (or load from disk) a bundle for each pair
bundles = {
    "R001__R002": FrozenPairBundle(
        pair_id="R001__R002",
        matching_drugs=["pregabalin", "sertraline"],  # canonical sorted
        matching_AEs=["somnolence"],
        global_stats=FrozenScopeStats(
            N_total=5000, N_drugs=200, N_AEs=300, N_drugs_AEs=40
        ),
        # country_stats and country_year_stats are optional;
        # set them if the pair has a shared country / country+year context
        country_stats=FrozenScopeStats(
            N_total=1200, N_drugs=50, N_AEs=80, N_drugs_AEs=12
        ),
        country_year_stats=FrozenScopeStats(
            N_total=300, N_drugs=15, N_AEs=22, N_drugs_AEs=4
        ),
    ),
    # ... one bundle per pair
}

offline_provider = FrozenStatsProvider(bundles)

runner = PipelineRunner(
    report_loader=your_report_loader,   # ReportLoader as usual
    stats_provider=offline_provider,    # no DB calls at eval time
)

result = runner.run_pair(pair)
```

No changes to `PipelineRunner` construction are required; the existing `ReportLoader` interface is unchanged.

### Freezing narrative source/case extractions

The per-report narrative source/case extractor (`extract_source_case`) calls an LLM once per unique report. For batches where reports appear in many pairs — or where extractor output must be reproduced exactly across eval runs — the secure repo can freeze the per-report results once and reuse them offline.

**Preprocessing step (online, once):**

```python
from llm_dup_dev.data.offline import (
    FrozenSourceCaseExtractions,
    save_frozen_source_case_extractions,
)
from llm_dup_dev.llm.tasks.narrative_source_case import extract_source_case

extractions = {}
for report in unique_reports_in_batch:
    extractions[report.report_id] = extract_source_case(
        llm_client,
        report.report_id,
        report.narrative,
        literature_reference=report.literature_reference,
    )

bundle = FrozenSourceCaseExtractions(extractions=extractions)
save_frozen_source_case_extractions(bundle, "source_case.json")
```

**Evaluation step (offline, repeatable):**

```python
from llm_dup_dev.data.offline import load_frozen_source_case_extractions

bundle = load_frozen_source_case_extractions("source_case.json")

runner = PipelineRunner(
    report_loader=your_report_loader,
    stats_provider=offline_provider,
    frozen_source_case_extractions=bundle,   # ← skips live LLM for covered reports
)
```

Partial bundles are allowed: `report_id` keys present in the bundle are served from it, and missing keys fall back to live extraction via `llm_client` (or to `None` if no client is configured). A `None` extraction does not fail the pair — the source-context evidence is simply absent and the decision rests on the rarity-based L_match evidence alone.

> **⚠ Silent disable when neither path is wired.** With `llm_client=None` *and* no `frozen_source_case_extractions` covering the pair, the source-context evidence (including DOI-based linkage) never contributes — there is no error, just a missing `source_case_assessment` block in `features.json` and no source-context zone in the explanation. `PipelineRunner.run_batch` emits a `WARNING` on the `llm_dup_dev.pipeline.runner` logger when this configuration is detected, and the saved run manifest records `feature_flags.source_case_override_enabled = false`. For deployments that intentionally run without an LLM client at inference time, see *Wiring without an LLM client* below.

The JSON artifact matches `schemas/frozen_source_case_extractions.schema.json` and is validated by `FrozenSourceCaseExtractions.model_validate_json` on load, so schema drift between preprocessing and evaluation surfaces as a `ValidationError` rather than silent misinterpretation.

### Narrative demographics extractor

A parallel per-report extractor, `extract_narrative_demographics`, pulls candidate patient **age**, **sex**, **weight**, and **height** values (each with an `evidence` snippet) from a single narrative. Its deterministic consumer is a merge helper rather than a pair-level comparator:

```python
from llm_dup_dev.llm.tasks.narrative_demographics import (
    extract_narrative_demographics,
)
from llm_dup_dev.feature_generation.narrative_demographics import (
    merge_narrative_demographics,
)

extraction = extract_narrative_demographics(
    llm_client, report.report_id, report.narrative
)
enriched_report = merge_narrative_demographics(report, extraction)
```

`merge_narrative_demographics` returns a copy of the `Report` with the extracted values appended to the existing `patient_*_values` lists, tagged with `sources=["narrative_llm"]`. Because the structured inconsistency checks already use min-difference / set-intersection across all candidate values, the narrative candidates participate automatically.

`PipelineRunner.run_batch` performs this extract-and-merge per report automatically: each unique report in a batch is extracted once (memoized across pairs) and merged into its `Report` before feature generation. Callers do not need to invoke `extract_narrative_demographics` or `merge_narrative_demographics` by hand — constructing the runner with an `llm_client` is sufficient.

### Freezing narrative demographics extractions

Parallel to source/case, per-report narrative demographics can be frozen once and reused across offline evaluation runs. The same graceful-partial semantics apply: covered `report_id` keys skip the live LLM; missing keys fall back to live extraction (or to an empty `NarrativeDemographics()` when no LLM client is configured).

**Preprocessing step (online, once):**

```python
from llm_dup_dev.data.offline import (
    FrozenNarrativeDemographics,
    save_frozen_narrative_demographics,
)
from llm_dup_dev.llm.tasks.narrative_demographics import (
    extract_narrative_demographics,
)

extractions = {}
for report in unique_reports_in_batch:
    extractions[report.report_id] = extract_narrative_demographics(
        llm_client, report.report_id, report.narrative
    )

bundle = FrozenNarrativeDemographics(extractions=extractions)
save_frozen_narrative_demographics(bundle, "narrative_demographics.json")
```

**Evaluation step (offline, repeatable):**

```python
from llm_dup_dev.data.offline import load_frozen_narrative_demographics

bundle = load_frozen_narrative_demographics("narrative_demographics.json")

runner = PipelineRunner(
    report_loader=your_report_loader,
    stats_provider=offline_provider,
    frozen_narrative_demographics=bundle,   # ← skips live LLM for covered reports
)
```

The JSON artifact matches `schemas/frozen_narrative_demographics.schema.json` and is validated by `FrozenNarrativeDemographics.model_validate_json` on load.

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

        May also populate:
        - reported_overall_ae_onset_date (PartialDate or None) — the
          explicit case-level adverse-event onset date as reported on
          the source report. For US reports this is typically extracted
          from the narrative pattern "CASE EVENT DATE: YYYYMMDD". When
          present, this overrides the dev-side derived overall AE onset
          date used by the clinical-date inconsistency module; when
          omitted (None), the dev-side fallback derivation is applied.
          Use the standard PartialDate resolution convention (year /
          month / day).
        - literature_reference (str or None) — a structured citation /
          source descriptor for literature reports (journal citation,
          registry/study label, DOI / PMID / NCT). When supplied it is
          passed alongside the narrative into the per-report
          source/case extractor as supplemental source-identifying
          metadata; when omitted (None), the extractor falls back to
          narrative-only behavior. This is an input field on the Report
          contract — the source/case extractor's output schema is
          unchanged.

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
| `patient_sex_values` | Structured field + LLM extraction | Use `sources=["structured"]` or `sources=["narrative_llm"]`. Dev-side normalization keeps only `"male"`/`"female"` (case-insensitive) and drops uncertainty values (`"unknown"`, `"not known"`, …) and `"other"` as missing, so the loader need not special-case them. |
| `country_of_origin_values` | Structured field + LLM extraction | Use `sources=["structured"]` or `sources=["narrative_llm"]`. Dev-side normalization drops uncertainty values (`"unknown"`, `"not known"`, …) as missing; real country values pass through unchanged. |
| `patient_age_values` | Structured + LLM | Include `unit` ("years", "months", "days") |
| `patient_height_values` | Structured + LLM | Values in **centimetres (cm)** |
| `patient_weight_values` | Structured + LLM | Values in **kilograms (kg)** |
| `drugs[].substance` | WHODrug active substance name | Case-insensitive matching is handled downstream |
| `drugs[].role` | Drug characterisation in the report | `"suspected"`, `"interacting"`, `"concomitant"`, or `null` (unknown). Used by drug-set inconsistency check. |
| `adverse_events[].pt_name` | MedDRA Preferred Term | Case-insensitive matching is handled downstream |
| `adverse_events[].primary_hlt_name` / `primary_hlgt_name` / `primary_soc_name` | Primary MedDRA hierarchy path for the PT (`PRIMARY_SOC_FG = 'Y'`). Optional — leave as `None` when unavailable | Used by the deterministic AE clinical-picture assessor (rule-based fallback). Populate from `MEDDRA_MD_HIERARCHY` when the secure side has access; the dev-side comparison is case-folded. Use the canonical full SOC name (e.g. `"Investigations"`, `"Cardiac disorders"`), not the abbreviation, so the Investigations-SOC exclusion works. |
| `report_date_values` | Structured | Set `resolution` to "day", "month", or "year" based on available precision |
| `reported_overall_ae_onset_date` | Narrative extraction (e.g. `CASE EVENT DATE: YYYYMMDD` on US reports) | Optional `PartialDate`. When supplied it overrides the derived overall AE onset date in clinical-date inconsistency detection. Leave as `None` to fall back to dev-side derivation. |
| `narrative` | Free-text narrative from the report | Used by narrative analysis (future); set to `None` if unavailable |
| `literature_reference` | Optional structured citation / source descriptor for literature reports (journal citation, registry/study label, DOI / PMID / NCT) | Optional `str`. When supplied, passed alongside the narrative into the per-report source/case extractor as supplemental source-identifying metadata. Leave as `None` when unavailable. |

See `schemas/report.schema.json` for the full JSON schema and `examples/report_example.json` for a concrete example.

### 2. LLMClient (optional)

Enables LLM-based assessment of whether two reports describe a clinically inconsistent adverse-event picture. With the default `ae_clinical_picture_mode = "auto"`, omitting the client falls back to the deterministic, MedDRA-hierarchy-based assessor (`feature_generation/ae_clinical_picture_rules.py`) — see *AE clinical-picture mode* below. Selecting `mode = "llm"` explicitly opts out of the fallback: the pipeline then records `ae_clinical_picture_status = "not_assessed"` with `ae_clinical_picture_reason = "no_llm_client"` and the AE component contributes nothing to the classification.

Implement the `LLMClient` protocol from `llm_dup_dev.llm.protocols`:

```python
from llm_dup_dev.llm import LLMClient, LLMRequest, LLMResponse


class AzureOpenAIClient:
    """Implements LLMClient against Azure OpenAI."""

    def generate(self, request: LLMRequest) -> LLMResponse:
        # Call Azure OpenAI with structured-output parsing.
        # request.response_model is a Pydantic model; parse the response
        # and return LLMResponse(parsed=<model_instance>, raw_text=<str>).
        ...
```

The client is injected via `PipelineRunner(llm_client=...)`. The protocol is structural (`@runtime_checkable`), so any class implementing `generate(request: LLMRequest) -> LLMResponse` will satisfy it. The dev repo never instantiates a concrete client — it only calls `client.generate(...)`.

#### Sampling parameters and error mapping

Two rules that concrete clients must get right, because both affect whether a run's recorded configuration is truthful:

1. **`request.temperature is None` means omit the parameter.** Do not substitute a value. `None` is the default, because newer reasoning-style models reject any explicit temperature and accept only their own. An explicit temperature equal to the model's default and an omitted parameter produce the same call, but only omission lets the run manifest state honestly that no temperature was requested.

2. **Never silently coerce a temperature the model rejects.** If a caller passes `0.0` and the backend cannot serve it, raise `LLMConfigurationError`. Quietly sending the model's default instead would make the manifest claim a determinism the run did not have.

Map provider errors accordingly:

| Provider failure | Raise | Pipeline behaviour |
|---|---|---|
| Non-retryable 4xx — unsupported parameter or value, unknown deployment, auth failure | `LLMConfigurationError` | Aborts the run immediately, even under `continue_on_error` |
| Transient — rate limit, timeout, 5xx | anything else | Tolerated per report/pair; that item's LLM features are empty |

The distinction matters because per-report tolerance is what stops one malformed narrative from taking down a batch. Applied to a misconfiguration it does the opposite: the run completes "successfully" with every LLM-derived feature empty, which downstream is indistinguishable from legitimately absent data. A run in which no LLM call ever succeeded is logged at ERROR for the same reason.

`LLMInfo.default_temperature` returned from `info()` should report the temperature the backend **actually applies**, not the one requested — for a temperature-pinned model, its fixed default. Paired with the manifest's `feature_flags["llm_temperature_requested"]`, that is what lets a reader tell a reproducible run from a non-reproducible one after the fact.

Determinism is therefore opt-in on both sides: pass `PipelineRunner(llm_temperature=0.0)` *and* use a model that accepts it. The frozen-extraction scripts expose the same choice as `--temperature`.

**PairFeatures output fields added by this module:**

| Field | Type | Description |
|-------|------|-------------|
| `ae_clinical_picture_status` | `"assessed"` / `"not_assessed"` | Whether the AE clinical-picture assessment ran |
| `ae_clinical_picture_reason` | str | Refines status: `""` (LLM-assessed), `"no_llm_client"`, `"trivially_matched"`, `"llm_error"`, `"rule_based"`, `"llm_fallback_to_rule"`, `"missing_hierarchy"` |
| `ae_clinical_picture_method` | `""` / `"llm"` / `"rule_based"` | Which assessor produced the result |
| `ae_clinical_picture_counts` | `AEClinicalPictureCounts` | Finding counts by severity (minor / moderate / strong / decisive), after deduplication |
| `ae_clinical_picture_rule_based_details` | `RuleBasedAEClinicalPictureDetails` or `null` | Step-1 / Step-2 audit trail (overlap level, U/M counts, missing-hierarchy warnings); populated only when `method == "rule_based"` |

AE findings are also included in the unified `PairFeatures.inconsistencies` list alongside rule-based inconsistencies, and their counts are merged into `PairFeatures.inconsistency_counts`.

#### AE clinical-picture mode

`PipelineRunner(ae_clinical_picture_mode=...)` selects the assessor:

| Mode | Behaviour |
|------|-----------|
| `"auto"` *(default)* | Use the LLM task when an `LLMClient` is configured. Otherwise — and on LLM exception — fall back to the deterministic `feature_generation/ae_clinical_picture_rules.py` assessor with `reason = "llm_fallback_to_rule"`. |
| `"llm"` | LLM-only. No fallback: missing client or LLM error → `not_assessed`. Preserves pre-fallback behaviour. |
| `"rule_based"` | Always use the deterministic, MedDRA-hierarchy-based assessor. Suitable for organisations or settings where LLM use is not possible. Requires `adverse_events[].primary_hlt_name` / `primary_hlgt_name` / `primary_soc_name` to be populated; without hierarchy on any PT the assessor returns `not_assessed("missing_hierarchy")`. |

The deterministic assessor implements a two-step comparison:

1. **Step 1 (symmetric, includes Investigations PTs)** — most-granular shared MedDRA level across all PT × PT pairs: PT match → no inconsistency; HLT → minor; HLGT → moderate; SOC → strong; nothing shared → decisive.
2. **Step 2 (directional, excludes Investigations PTs)** — counts unmatched non-Investigations PTs (`U`) vs. PTs whose HLT is present in the other report (`M`); severity is `none` (`U=0`), `minor` (`U=1` or `U/(U+M) < 0.5`), or `moderate` otherwise. Computed independently for `R1 → R2` and `R2 → R1`.

See `duplicate_adjudication_spec.md` § *AE clinical-picture inconsistency* for the full algorithm and rationale.

##### Wiring through `default_config.yaml`

For deployments that flip between LLM and deterministic AE assessment per environment (e.g. an org that cannot use LLMs for AE clinical-picture specifically, but still uses one for the per-report narrative extractions), expose the mode in the secure repo's `configs/default_config.yaml`:

```yaml
# Selects the AE clinical-picture assessor.
# One of: "llm" | "rule_based" | "auto" (default).
ae_clinical_picture_mode: rule_based
```

Forward the value in the secure-side `make_pipeline()` factory:

```python
import yaml
from llm_dup_dev.pipeline.runner import PipelineRunner

with open("configs/default_config.yaml") as f:
    cfg = yaml.safe_load(f) or {}

runner = PipelineRunner(
    report_loader=loader,
    stats_provider=stats,
    llm_client=llm_client,  # still used for narrative / source-case / demographics
    ae_clinical_picture_mode=cfg.get("ae_clinical_picture_mode", "auto"),
)
```

Keep this knob in the YAML, **not** in `PipelineConfig`: it's an orchestration / deployment switch (which assessor to dispatch), not a feature/scoring parameter. `PipelineConfig` stays focused on `DomainSizeConfig`, `InconsistencyThresholds`, `AnchorConfig`, etc.

When the key is absent, the loader's `dict.get(..., "auto")` preserves backwards compatibility with existing YAML files.

If a deployment instead drops the `llm_client` entirely — for example, an environment that cannot use LLMs at all at inference time — see *Wiring without an LLM client* below for the required frozen sidecars.

##### Wiring without an LLM client

Some deployments must run inference without any LLM. With `ae_clinical_picture_mode = "rule_based"` and `llm_client = None`, the AE clinical-picture path is fully deterministic — but the **source-context evidence** and **narrative-demographics merge** still need their per-report LLM extractions, which must therefore be produced **once at frozen-dataset creation time** and serialized as sidecars next to `validation_reports.json`. Without those sidecars the source-context evidence silently disables (see *Freezing narrative source/case extractions* above) and `PipelineRunner.run_batch` logs a `WARNING` on first use.

Two helper scripts ship with the dev repo for this purpose:

```sh
# At frozen-dataset creation time (online, with an LLM client):
python scripts/extract_frozen_source_case.py \
    --reports validation_reports.json \
    --llm-factory my_secure_pkg.llm:make_client \
    --out source_case.json

python scripts/extract_frozen_narrative_demographics.py \
    --reports validation_reports.json \
    --llm-factory my_secure_pkg.llm:make_client \
    --out narrative_demographics.json
```

`--llm-factory` takes a `module:callable` reference. The callable is imported and invoked with no arguments and must return an object satisfying the `LLMClient` protocol — the secure repo supplies its own factory; the dev repo never instantiates a client.

Inference step (offline, deterministic, no `llm_client`):

```python
from llm_dup_dev.data.offline import (
    load_frozen_narrative_demographics,
    load_frozen_source_case_extractions,
)
from llm_dup_dev.pipeline.runner import PipelineRunner

runner = PipelineRunner(
    report_loader=loader,
    stats_provider=stats,
    llm_client=None,
    ae_clinical_picture_mode="rule_based",
    frozen_source_case_extractions=load_frozen_source_case_extractions(
        "source_case.json"
    ),
    frozen_narrative_demographics=load_frozen_narrative_demographics(
        "narrative_demographics.json"
    ),
)
```

Omitting either bundle in this configuration silently disables the corresponding feature: missing source/case → no source-context evidence (no DOI-based linkage); missing narrative demographics → no LLM-derived patient age/sex/weight/height candidates merged into reports. The runner emits a `WARNING` on the `llm_dup_dev.pipeline.runner` logger in either case, and the saved run manifest records `feature_flags.source_case_override_enabled = false` / `feature_flags.narrative_demographics_extraction_enabled = false` so the disabled state is auditable in archived runs.

### 3. StatsProvider

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

### Live mode (single pair)

```python
from llm_dup_dev.pipeline.runner import PipelineRunner
from llm_dup_dev.models.report_pair import ReportPair

# Live mode: SqlStatsProvider queries the database at runtime
runner = PipelineRunner(
    report_loader=SqlReportLoader(conn_str),
    stats_provider=SqlStatsProvider(conn_str),
    llm_client=my_azure_client,   # optional; with default mode="auto" the deterministic
                                  # rule-based assessor is used as a fallback when omitted
    # ae_clinical_picture_mode="auto",  # "llm" | "rule_based" | "auto" (default)
)

# Process a single pair
pair = ReportPair(
    pair_id="12345__67890",
    report_1_id="12345",
    report_2_id="67890",
)
result = runner.run_pair(pair)

print(result.final_label)          # "Duplicates", "Otherwise_related", "Unrelated", "Abstain"
print(result.features.L_match)     # e.g. 4.2
print(result.features.L_patient)   # e.g. 2.3 — patient-specific evidence
print(result.classification.explanation)  # human-readable reasoning

# The inconsistency arithmetic behind the decision, as the classifier recorded it
penalty = result.classification.penalty
print(penalty.w_tot)               # e.g. 1.0
print(penalty.threshold)           # e.g. 4.0 — None if w_tot was never compared
print(penalty.satisfied)           # True when w_tot < threshold; None if not compared
for term in penalty.terms:
    print(term.severity, term.count, term.weight)   # weights the run applied
```

Read `penalty` rather than parsing `explanation`. The explanation remains the
human-readable surface and still quotes `w_tot`, but it is prose and its wording
is not a contract. `penalty` carries the weights the run actually used, so the
arithmetic can be reproduced without knowing the run's `DecisionThresholds`.

For **offline/frozen-validation** mode, replace `SqlStatsProvider` with
`FrozenStatsProvider` — see [Offline frozen-validation mode](#offline-frozen-validation-mode) above.

### Batch processing (live or offline)

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

--- Reference: 3x3 Confusion ---
Confusion (rows=predicted, cols=actual):
                     Duplicates  Otherwise_related  Unrelated
         Duplicates          58                  2          3
  Otherwise_related           4                 12          5
          Unrelated           1                  7         46

--- Primary: detecting Non-duplicates ---
  Positive class: Non_duplicates (Otherwise_related + Unrelated collapsed)
  Pairs in this view: 138
  Precision:       0.93
  Recall:          0.93
  F1:              0.93
  Specificity:     0.92
  False positives: 5
  False negatives: 5

Confusion (rows=predicted, cols=actual):
                      Non_duplicates  Duplicates
      Non_duplicates              70           5
          Duplicates               5          58

--- Complement: detecting Duplicates ---
  Positive class: Duplicates (same 2x2 partition, polarity flipped)
  Pairs in this view: 138
  Precision:       0.92
  Recall:          0.92
  F1:              0.92
  Specificity:     0.93
  False positives: 5
  False negatives: 5

Confusion (rows=predicted, cols=actual):
                      Duplicates  Non_duplicates
          Duplicates          58               5
      Non_duplicates           5              70
```

Metrics are quoted to two significant figures. Accuracy is
deliberately not reported: the 3-class accuracy is dominated by the
majority class and hides the precision / recall trade-off that matters
for duplicate detection. The primary view treats Non_duplicates (the
union of Otherwise_related and Unrelated) as the positive class of
interest, so recall is the proportion of actual non-duplicates the
pipeline correctly classifies as such. The complement reports the
same 2x2 partition with the polarity flipped — positive class
Duplicates — so recall there is the proportion of actual duplicates
correctly flagged.

### Eval annotation format

Each annotation dict must contain:

| Field | Type | Values |
|-------|------|--------|
| `pair_id` | str | Must match pipeline output |
| `duplicates_assessment` | str | "duplicates", "otherwise_related", "unrelated", "abstain" |

The pipeline's two internal assessments — an `IndependenceAssessment`
(`Non-independent / Independent`), rejected on shared provenance or on
the rarity-based matching evidence, plus a `RelationshipAssessment`
(`Same_case / Different_case / Abstain`) produced when independence has
been rejected — are mapped to the user-facing label by
`ClassificationResult.final_label`:

| Independence / relationship assessment | User-facing final label |
|-----------------|------------------|
| Non-independent + Same_case | `Duplicates` |
| Non-independent + Different_case | `Otherwise_related` |
| Non-independent + Abstain | `Abstain` |
| Independent | `Unrelated` |

Eval consumers should compare against `result.final_label` rather than the internal `independence_assessment` / `relationship_assessment` fields.

### Threshold calibration (offline)

`classify()` is a pure function of `(PairFeatures, DecisionThresholds)`, so once a
batch has run you can replay the decision under different thresholds without
re-running the pipeline — no reports, no database, no LLM. Persist the features
once, then sweep as often as you like:

```python
from llm_dup_dev.pipeline.export import write_pair_features

batch = runner.run_batch(pairs)
write_pair_features(batch.results, "features.json")   # do this once
```

```python
from llm_dup_dev.eval import (
    AbstainMode, grid_over, load_annotations, sweep, sweep_to_text,
)
from llm_dup_dev.pipeline.export import read_pair_features

features = read_pair_features("features.json")
annotations = load_annotations("annotations.csv")

result = sweep(
    features,
    annotations,
    grid=grid_over(lpatient_compelling=[1.5, 2.0, 2.5, 3.0]),
    abstain_mode=AbstainMode.DECIDED_ONLY,
)
print(sweep_to_text(result))
```

`write_pair_features` also produces the file `review_html --features` expects, so
the same artifact drives the review pages.

**Reading a sweep — three traps.** Each is reported rather than silently
absorbed, but the judgement is yours:

- **No row is marked best, deliberately.** Under `DECIDED_ONLY` an abstained pair
  leaves `n` entirely, so a setting that abstains on the pairs it would have got
  wrong posts a *better* F1 on a smaller denominator. F1 is not comparable across
  rows whose `n` differs. Read `n` and `abstained` together; there is no `argmax`
  helper because it would reliably pick the setting that refuses the most pairs.
- **Check coverage first.** `pair_id` is `{report_id1}__{report_id2}` and nothing
  canonicalises the order, so an annotation file listing a pair the other way
  round matches nothing and is silently unscored. The report warns when
  annotations match only in reversed orientation, or not at all.
- **Invalid grid points are skipped, not fatal.** `DecisionThresholds` requires
  `w_decisive` to exceed every zone threshold, so a rectangular grid has invalid
  corners. They appear under "Skipped" with the reason.

`grid_over(base, **axes)` builds the rectangular grid. Note the inconsistency
weights and the `w_tot_*_zone` thresholds are not independent — only their ratios
matter — so vary one side, not both.

### Eval annotation format (calibration)

`load_annotations` reads a delimited file (tab, comma or semicolon — sniffed)
identifying each pair by its two report ids:

```
report_id1	report_id2	duplicates_assessment
R001	R002	duplicates
R003	R004	unrelated
```

`pair_id` is derived as `{report_id1}__{report_id2}`. Any additional columns
(annotator, notes, …) are preserved on the loaded dict and ignored by scoring, so
they remain available for stratifying an analysis.

Two conditions fail at load rather than mid-run: a repeated pair (evaluation
indexes by `pair_id` and would silently keep whichever row came last) and a label
outside `duplicates` / `otherwise_related` / `unrelated` / `abstain`.

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
        age_years_range=80,          # plausible ages 0–80 years
        age_months_range=100,        # plausible infant months
        sex_categories=2,
        child_age_threshold_years=15,
        height_cm_range_child=20,
        height_cm_range_adult=40,
        weight_kg_range_child=20,
        weight_kg_range_adult=40,
    ),
    inconsistency_thresholds=InconsistencyThresholds(
        age_moderate=3.0,   age_strong=10.0,
        height_moderate=5.0, height_strong=10.0,
        weight_moderate=5.0, weight_strong=15.0,
    ),
    anchor=AnchorConfig(
        threshold=50,
    ),
    epsilon=1e-15,
)

thresholds = DecisionThresholds(
    lmatch_compelling=3.0,    # compelling evidence zone
    lmatch_intermediate=2.0,  # intermediate evidence zone
    lmatch_limited=1.0,       # limited evidence zone
    lpatient_compelling=2.0,  # L_patient bar (Unresolved source-context zone; not an L_match zone)
    w_moderate=1.0,           # weight for moderate inconsistencies
    w_strong=3.0,             # weight for strong inconsistencies
    w_tot_compelling_zone=4.0,    # w_tot threshold for compelling zone
    w_tot_intermediate_zone=3.0,  # w_tot threshold for intermediate zone
    w_tot_limited_zone=1.0,       # w_tot threshold for limited zone
)

runner = PipelineRunner(
    report_loader=loader,
    stats_provider=stats_provider,
    pipeline_config=config,
    decision_thresholds=thresholds,
)
```

### Decision logic summary

The decision logic answers two questions in sequence: an **independence
assessment** (Non-independent when a shared-source verdict is present or
`L_match > lmatch_limited`; else Independent → `Unrelated`), then — for
non-independent pairs — a **relationship assessment** comparing the
weighted inconsistency score against the tolerance of the applicable
evidence zone:

    w_tot = w_minor × minor + w_moderate × moderate + w_strong × strong
    (defaults: 0.4 × minor + 1 × moderate + 3 × strong)

| L_match zone | Same_case (`Duplicates`) if... | Otherwise → |
|---|---|---|
| > 3.0 (compelling)   | `w_tot < 4` | Different_case (`Otherwise_related`) |
| > 2.0 (intermediate) | `w_tot < 3` | Different_case (`Otherwise_related`) |
| > 1.0 (limited)      | `w_tot < 1` | Different_case (`Otherwise_related`) |
| ≤ 1.0                | never | Independent (`Unrelated`) — unless a shared-source verdict rejects independence |

Source-context evidence zones (Identity / Distinct / Unresolved, derived
from the shared-source verdict) take priority over these rarity zones —
see the spec § Relationship classifier for the full priority-ordered
table. Decisive inconsistencies (e.g. distinct patient IDs from the same
study) preclude `Same_case`. The independence assessment then decides
the surface label: `Otherwise_related` when independence is rejected,
`Unrelated` otherwise.

---

## Run manifests

A **run manifest** is a small YAML/JSON file written alongside a run's outputs that records *what was actually run*: the resolved pipeline configuration, decision thresholds, feature flags, dev-side package version and git commit, input/output paths, and any deployment-specific metadata supplied by the secure repo. The intent is reproducibility — given a results folder, a future operator can see which knobs were active without consulting external systems.

Secure deployments should save **one `run_manifest.yaml` per run folder**. The contract:

- The manifest must record **effective configuration** — the values that the pipeline actually used after defaults and overrides — not a hand-written intent file.
- The manifest must **not contain secrets**. The utility redacts obvious secret-looking keys (`api_key`, `secret`, `token`, `password`, `connection_string`, …) and warns, but the call site should never pass them in the first place.
- Deployment-specific metadata (secure repo commit, SQL loader version, Azure OpenAI deployment name, notebook/script name, command-line invocation) goes into `secure_context: dict[str, Any]`. The dev repo defines the field but does not prescribe its keys.
- Data provenance (source database name + snapshot, MedDRA version, extraction timestamp) is *not* a `secure_context` key — it has first-class typed fields. See [Dataset manifests](#dataset-manifests) below. A run manifest references its dataset manifest via `dataset_ref`.

### Usage

```python
import sys
from pathlib import Path

from llm_dup_dev.pipeline.runner import PipelineRunner
from llm_dup_dev.run_manifest import (
    DatasetRef,
    build_run_manifest,
    write_run_manifest,
)

runner = PipelineRunner(
    report_loader=loader,
    stats_provider=stats,
    llm_client=llm_client,
    pipeline_config=pipeline_config,
    decision_thresholds=decision_thresholds,
    ae_clinical_picture_mode="auto",
)
batch = runner.run_batch(pairs)

results_dir = Path("outputs/eval_2026-04-27")

manifest = build_run_manifest(
    runner=runner,
    input_paths={"frozen_bundles": str(bundles_path)},
    output_paths={"results_dir": str(results_dir)},
    # The run's *live inference-time* model (e.g. the AE clinical-picture LLM).
    # May be an LLMInfo, a Pydantic model, or a dict. When omitted it is
    # auto-pulled from the runner's LLM client `info()`.
    llm_config={
        "deployment": "gpt-4o-prod",
        "model": "gpt-4o",
        "temperature": 0.0,
    },
    secure_context={
        "secure_repo_commit": secure_sha,
        "sql_loader_version": "2.3.1",
        "notebook": "eval_run_2026-04-27.ipynb",
        "invocation": " ".join(sys.argv),
    },
    # DatasetRef.from_manifest(dataset_manifest) copies the join key, the
    # database/MedDRA fields, and the dataset's *extraction-time* llm_config —
    # a separate slot from the run's inference-time llm_config above.
    dataset_ref=DatasetRef.from_manifest(
        dataset_manifest, relative_path="../dataset_manifest.yaml"
    ),
    notes="Quarterly eval refresh",
)
write_run_manifest(results_dir, manifest, formats=("yaml", "json"))
```

`build_run_manifest` extracts the resolved `PipelineConfig` and `DecisionThresholds` directly from the runner, so the manifest reflects defaults that were filled in alongside any overrides you passed. Auto-populated fields (`schema_version`, `created_at`, `run_id`, `dev_package.version`, `dev_git_commit`) require no caller action; missing git metadata or package metadata degrade to `None` rather than raising.

`write_run_manifest` creates the output directory if needed and accepts any combination of `"yaml"` and `"json"` in `formats` (default: YAML only).

### Manifest fields

| Field | Type | Source | Meaning |
|---|---|---|---|
| `schema_version` | str | constant | Bump when the manifest layout changes incompatibly. Currently `"1.2"`. |
| `created_at` | datetime (UTC) | auto / caller | When the manifest was built. |
| `run_id` | str | auto / caller | Stable identifier for this run. Defaults to a 12-char hex token. |
| `run_name` | str \| null | caller | Optional human-readable label. |
| `dev_package.name` | str | auto | Always `"llm-dup-dev"`. |
| `dev_package.version` | str \| null | auto | From `importlib.metadata`; `null` if the package is not installed via metadata (e.g. PYTHONPATH dev runs). |
| `dev_git_commit` | str \| null | auto | Short SHA of the dev repo's HEAD; `null` if git is unavailable. |
| `input_paths` | dict | caller | Free-form. Suggested keys: `frozen_bundles`, `frozen_source_case`, `frozen_narrative_demographics`, `report_loader`. |
| `pipeline_config` | dict | runner / caller | `PipelineConfig.model_dump()` — *effective* values, defaults included. |
| `decision_thresholds` | dict | runner / caller | `DecisionThresholds.model_dump()` — *effective* values. |
| `feature_flags` | dict | runner / caller | When built from a runner: `ae_clinical_picture_mode`, `has_llm_client`, `has_frozen_source_case`, `has_frozen_narrative_demographics`. Caller-supplied keys merge on top. |
| `llm_config` | dict \| null | caller / auto | The run's **live inference-time** LLM (deployment, model, temperature, max_tokens). Auto-pulled from the runner's client `info()` when not supplied; an explicit value may be an `LLMInfo`, a Pydantic model, or a dict. `null` for a fully-offline run. This is a *distinct slot* from the dataset's extraction-time model (`dataset_ref.llm_config`) — the two are never merged. Secret-looking keys are auto-redacted. |
| `output_paths` | dict | caller | Free-form. Suggested keys: `results_dir`, `review_html_dir`, `eval_summary`. |
| `secure_context` | dict \| null | caller | Deployment-specific metadata. The dev repo does not prescribe keys; suggested values: `secure_repo_commit`, `sql_loader_version`, `azure_deployment`, `notebook`, `invocation`. Do **not** put `database_name`, `database_snapshot`, or `meddra_version` here — use `dataset_ref` instead. |
| `dataset_ref` | object \| null | caller | Pointer to the parent dataset manifest. `dataset_id` is the authoritative join key; `database_name`, `database_snapshot`, `meddra_version` and `llm_config` (the dataset's **extraction-time** model) are duplicated for at-a-glance comparison and self-describing archived runs. Build via `DatasetRef.from_manifest(...)`. See [Dataset manifests](#dataset-manifests). |
| `notes` | str \| null | caller | Free-form prose. |

### Secret redaction

Keys in `llm_config`, `dataset_ref.llm_config`, and `secure_context` whose names contain any of the following (case-insensitive substring match) are replaced with the literal string `"***REDACTED***"` and a `UserWarning` is emitted: `api_key`, `apikey`, `secret`, `token`, `password`, `passwd`, `credential`, `bearer`, `authorization`, `client_secret`, `connection_string`. Redaction recurses into nested dicts and lists. Treat the warning as a code smell — fix the call site rather than relying on the safety net.

### Schema-version policy

Bump `schema_version` only when the manifest's layout changes in a way that breaks downstream consumers (renamed fields, removed fields, type changes). Adding new optional fields does not require a bump.

---

## Dataset manifests

A **dataset manifest** records the provenance of a *validation set* — the source database (name + snapshot/version) and the MedDRA version used to extract its reports and frequency statistics. It lives once at the root of the validation set folder. Each run under that folder carries a `dataset_ref` on its `run_manifest.yaml` linking back via `dataset_id`.

Why both files: the dataset manifest is the canonical record (one per validation set, no duplication); the `dataset_ref` keeps each run folder self-describing when copied or archived in isolation.

### Validation set folder layout

The dev repo does not enforce a layout — secure-side scripts choose paths. The expected shape:

```
validation_sets/
└── vs_2026Q1/
    ├── dataset_manifest.yaml          ← canonical provenance
    ├── run_2026-04-27_baseline/
    │   ├── run_manifest.yaml          ← dataset_ref → dataset_manifest
    │   └── …
    └── run_2026-04-28_new_thresholds/
        ├── run_manifest.yaml          ← same dataset_id
        └── …
```

### Usage

```python
from datetime import datetime, timezone
from pathlib import Path

from llm_dup_dev.dataset_manifest import (
    DatasetSnapshot,
    build_dataset_manifest,
    write_dataset_manifest,
)

vs_dir = Path("validation_sets/vs_2026Q1")

dataset_manifest = build_dataset_manifest(
    dataset_snapshot=DatasetSnapshot(
        database_name="VigiBase",
        database_snapshot="2026Q1",
        meddra_version="27.0",
        extracted_at=datetime.now(timezone.utc),
    ),
    dataset_name="vs_2026Q1",
    # The *extraction-time* LLM that produced this dataset's frozen LLM
    # artifacts (narrative-demographics / source-case). May be an LLMInfo or dict.
    llm_config={"provider": "azure_openai", "model": "gpt-5.4"},
    secure_context={"secure_repo_commit": secure_sha},
    notes="Quarterly validation set",
)
write_dataset_manifest(vs_dir, dataset_manifest, formats=("yaml", "json"))

# Then, when launching each run under vs_dir, pass a DatasetRef pointing to
# this manifest via build_run_manifest(..., dataset_ref=DatasetRef(...)).
```

### Manifest fields

| Field | Type | Source | Meaning |
|---|---|---|---|
| `schema_version` | str | constant | Currently `"1.1"`. |
| `created_at` | datetime (UTC) | auto / caller | When the manifest was built. |
| `dataset_id` | str | auto / caller | Stable join key. Defaults to a 12-char hex token; every run under the validation set references this id via `dataset_ref.dataset_id`. |
| `dataset_name` | str \| null | caller | Optional human-readable label for the validation set. |
| `dataset_snapshot.database_name` | str | caller | Source database (e.g. `"VigiBase"`). |
| `dataset_snapshot.database_snapshot` | str | caller | Snapshot/version tag — opaque to the dev repo (e.g. `"2026Q1"`, a date string). |
| `dataset_snapshot.meddra_version` | str | caller | MedDRA version used to extract reports and stats (e.g. `"27.0"`). |
| `dataset_snapshot.extracted_at` | datetime | caller | When reports were pulled from the DB. |
| `dev_package.*`, `dev_git_commit` | … | auto | Same semantics as on `RunManifest`. |
| `llm_config` | dict \| null | caller | The **extraction-time** LLM that produced this dataset's frozen LLM artifacts (provider, model, deployment, …). May be an `LLMInfo`, a Pydantic model, or a dict. Runs that consume this dataset copy it into `RunManifest.dataset_ref.llm_config` (use `DatasetRef.from_manifest`). Secret-looking keys are auto-redacted. |
| `secure_context` | dict \| null | caller | Deployment-specific metadata, same redaction rules as on `RunManifest`. |
| `notes` | str \| null | caller | Free-form prose. |

### Migration

Existing validation sets predating this contract have no `dataset_manifest.yaml`, and their run manifests have `dataset_ref=None`. That `None` is the explicit signal that the dataset's provenance is unknown.

---

## Generating HTML review pages

The `review_html` module produces standalone HTML files for human inspection of suspected duplicate pairs. It is a dev-side implementation — the secure repo only needs thin wrapper code to load `Report` objects and call the renderer. No database access occurs inside the renderer.

### When to use it

| Workflow | Description |
|----------|-------------|
| **Precision testing** | Inspect a sample of pairs classified as duplicates to assess false-positive rate |
| **Pipeline refinement** | Examine borderline pairs (low L_match, inconsistencies) to inform threshold tuning |
| **Human annotation** | Generate review pages for human annotators |
| **Ad hoc pair inspection** | Quickly review any pair by report ID |

### Single pair

```python
from llm_dup_dev.review_html import render_pair_html
from pathlib import Path

# report_1, report_2: Report objects loaded by your SqlReportLoader
path = render_pair_html(
    report_1,
    report_2,
    pair_id="8047905-7215592",
    output_dir=Path("outputs/review"),
)
# → outputs/review/8047905-7215592.html
```

Pass optional pipeline outputs to add evidence and classification sections:

```python
path = render_pair_html(
    report_1,
    report_2,
    pair_id="8047905-7215592",
    features=result.features,                  # PairFeatures from PipelineRunner
    classification=result.classification,      # ClassificationResult
    external_url=f"https://vigilyze.who-umc.org/?umcreportids={r1_id},{r2_id}",
    external_url_label="View in Vigilyze",
    output_dir=Path("outputs/review"),
)
```

### Batch mode

```python
from llm_dup_dev.review_html import render_batch_html, PairRenderInput

items = [
    PairRenderInput(
        report_1=loader.load_report(pair.report_1_id),
        report_2=loader.load_report(pair.report_2_id),
        pair_id=pair.pair_id,
        features=results[pair.pair_id].features,
        classification=results[pair.pair_id].classification,
        external_url=f"https://vigilyze.who-umc.org/?umcreportids={pair.report_1_id},{pair.report_2_id}",
        external_url_label="View in Vigilyze",
    )
    for pair in pairs
]

output_dir = render_batch_html(items, output_dir=Path("outputs/review"))
# Writes one HTML per pair + an index.html linking to all of them
```

### Rendering evaluated pipeline results

When you have `PairResult` objects from the pipeline, use `render_result_html` / `render_results_html` to render pages with the full prediction/evaluation view (decision summary, detailed evidence, classification):

```python
from llm_dup_dev.review_html import render_result_html, render_results_html, ResultRenderInput

# Single result
path = render_result_html(
    result,                                # PairResult from PipelineRunner
    report_1,
    report_2,
    include_debug=True,                    # optional: collapsible raw JSON sections
    output_dir=Path("outputs/evaluated"),
)

# Batch of results
items = [
    ResultRenderInput(
        result=results[pair.pair_id],
        report_1=loader.load_report(pair.report_1_id),
        report_2=loader.load_report(pair.report_2_id),
        include_debug=True,
    )
    for pair in pairs
]
output_dir = render_results_html(items, output_dir=Path("outputs/evaluated"))
```

The evaluated-pair pages reuse the same side-by-side comparison layout and add:

- **Decision summary** — final label, L_match, inconsistency counts, clinical-date inconsistency counts, explanation
- **AE clinical-picture status panel** — assessment status, reason (if not assessed), and per-severity finding counts; suppressed findings are shown with a de-emphasised badge
- **Extended evidence** — anchor population details, probability breakdown, feature contributions, inconsistency counts, clinical-date inconsistency counts
- **Debug section** (optional) — collapsible raw JSON for PairFeatures and ClassificationResult

### Default output location

All rendering functions default to `outputs/review_html/`. Override with the `output_dir` parameter.

### CLI (from frozen JSON exports)

If reports and pipeline outputs have already been serialised to JSON:

```bash
python -m llm_dup_dev.review_html \
    --reports reports.json \
    --pairs pairs.json \
    --features features.json \
    --classifications classifications.json \
    --output-dir outputs/review/
```

**JSON formats:**

| File | Format |
|------|--------|
| `reports.json` | `{ "<report_id>": { ...Report fields... }, ... }` |
| `pairs.json` | `[ { "pair_id": "...", "report_1_id": "...", "report_2_id": "..." }, ... ]` |
| `features.json` | `{ "<pair_id>": { ...PairFeatures fields... }, ... }` (optional) |
| `classifications.json` | `{ "<pair_id>": { ...ClassificationResult fields... }, ... }` (optional) |

Use `Report.model_dump()`, `PairFeatures.model_dump()`, and `ClassificationResult.model_dump()` to produce these from the pipeline outputs.

### What the HTML shows

Each page contains:

- **Header** — pair ID, report IDs, optional external link (e.g. Vigilyze), optional classification badge
- **Demographics** — sex, age, country, report date, height, weight; green for matches, red for mismatches; only fields present in at least one report are shown
- **Drugs** — one row per substance in the union across both reports; start/end dates, role, form, strength shown inline; substance name is green (present in both), red (present in one only)
- **Adverse events** — same union logic; onset date in `(YYYY-MM-DD)` format where available
- **Narratives** — side-by-side free text (if present)
- **Evidence summary** — L_match, anchor, reporting context, inconsistencies (if `features` supplied)
- **Classification** — independence assessment, relationship assessment, final label, explanation (if `classification` supplied)

---

## Reference files

| File | Purpose |
|------|---------|
| `schemas/*.schema.json` | JSON schemas generated from Pydantic models |
| `examples/*_example.json` | Concrete example objects |
| `docs/duplicate_adjudication_spec.md` | Full method specification |
| `docs/pipeline_overview.md` | High-level pipeline description |
| `docs/data_contracts.md` | Data layer design and contracts |
| `docs/repository_roles.md` | Security controls between repos |
| `src/llm_dup_dev/review_html/__init__.py` | `render_pair_html` / `render_batch_html` API docstrings |

---

## Testing

### Run the test suite (in llm_dup_dev)

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
```

552 tests covering all modules.

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
| Narrative analysis (Phase 9) | Removed | A standalone LLM narrative-consistency assessment was built and then removed: its verdict never reached the decision logic, so it cost one LLM call per pair and surfaced only as a display field. The narrative signals that *do* feed classification are the AE clinical-picture module and the narrative source/case/demographics extractions, all already implemented via the `LLMClient` protocol. |
