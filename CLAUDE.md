# llm_dup_dev — Project Context for Claude

## Purpose
LLM-powered duplicate post-processing and annotation support for pharmacovigilance adverse 
event reports (VigiBase/FAERS).
Pairs of reports classified as suspected duplicates by the vigiMatch 2025 method
(see: https://doi.org/10.48550/arXiv.2504.03729) are further processed by
a large language model (e.g. GPT-4o), augmented with relevant descriptive statistics from 
the database of adverse event reports, is prompted to assess for each report pair e.g. whether 
the reports (i) likely refer to the same patien (or not); (ii) describe similar adverse events; 
and (iii) belong to a cluster of otherwise related reports. For each pair, the LLM generates
both explanatory free text and structured classifications constrained to a pre-specified 
JSON schema. An explicitly programmed decision logic operating solely on the structured outputs 
then classifies report pairs as   **Duplicates**, **Otherwise_related**, or **Unrelated** .

## Architecture Overview
We will maintain two physically separate repositories with distinct roles:
llm_dup_dev:	Prompt engineering, structured output design, evaluation logic
llm_dup_pv_secure: Annotation support, evaluation on real data, Azure execution

### Repository Roles and Controls
#### llm_dup_pv_secure (Secure Environment)

**Purpose**
<ul>
<li>Annotation notebooks for eval set creation</li>
<li>Access to real report extracts + summary statistics</li>
<li>Storage and analysis of eval sets</li>
<li>Azure-based LLM execution against real data</li>
<li>Metric computation</li>
<li>Structured Case-Level Examination (conducted exclusively within the secure environment)</li>
</ul>

**Controls**
<ul>
<li>Used only in VS Code without external AI coding assistants</li>
<li>No Claude extension installed</li>
<li>No external AI coding assistants connected to repo</li>
<li>Never opened in workspace or environment where external AI coding assistant is enabled.</li>
<li>.claudeignore file added as an additional precautionary safeguard, not the primary security control</li>
</ul>

#### llm_dup_dev (AI-Assisted Development Environment)
**Purpose**
<ul>
<li>Prompt design and iteration</li>
<li>Decision logic (structured reasoning schema) development</li>
<li>Post-processing logic</li>
<li>Evaluation harness development</li>
<li>Analysis of aggregated metrics</li>
</ul>
**Controls**
<ul>
<li>Accessed via the Claude Code app</li>
<li>Contains no real pharmacovigilance data</li>
<li>Uses only:
<ul>
<li>Synthetic, public, or abstracted examples
<li>Aggregated performance metrics
<li>Non-identifiable feature summaries
</ul>
</ul>

### Data Flow Model
llm_dup_dev (Claude-assisted)
        ↓
Code / prompt updates
        ↓
llm_dup_pv_secure (no AI)
        ↓
Results of Azure execution on real data
(aggregated, non-identifiable metrics only)
        ↓
llm_dup_dev (Claude-assisted)
 
### Additional Safeguards
<ul>
<li>Physical directory separation (~/llm_dup_dev/ and ~/llm_dup_pv_secure/)</li>
<li>No shared workspace roots</li>
<li>No multi-root workspaces spanning both repositories</li>
<li>.claudeignore files added to all folders in llm_dup_pv_secure</li>
<li>No symlinks or mounts between repositories</li>
<li>No copy-paste of real case text into llm_dup_dev</li>
</ul>

## Repository Structure
```
src/llm_dup_dev/
├── eval/                              # Evaluation framework (imported by secure repo)
│   ├── labels.py                      # Canonical label constants
│   ├── metrics.py                     # ConfusionMatrix, accuracy functions, EvalSummary
│   ├── comparison.py                  # evaluate() — main entry point
│   └── report.py                      # Text/markdown summary formatting
├── lib/
│   ├── data_extraction.py             # Fetches & structures individual reports from SQL Server
│   ├── data_extraction_demographics.py# Aggregate demographic profiles (joblib-cached SQL CTEs)
│   ├── decision_logic.py              # Deterministic classification decision tree
│   ├── fetch_study.py                 # Multi-source literature lookup (PubMed/Crossref/EuropePMC)
│   ├── html_rendering_functions.py    # HTML output generation
│   └── util.py                        # Shared helpers (JSON parsing, missing-value checks, study comparison)
└── vigiMatchContext/                  # LLM system prompts and annotation guidelines
    ├── prompt_system.txt              # LLM role definition
    ├── prompt_same_patient.txt        # Same-patient assessment prompt
    ├── prompt_AE_similarity.txt       # Adverse-event similarity prompt
    ├── prompt_clustering.txt          # Cluster-detection prompt
    ├── prompt_DecisionLogic.txt       # EXPERIMENTAL: LLM-based final classification (not production)
    ├── prompt_literature.txt          # Literature/study evaluation prompt
    ├── context01.txt                  # Full vigiMatch annotation guidelines
    ├── context_nn1.txt                # LEGACY — combined drug+AE prompt (superseded)
    └── context_nn1b.txt               # LEGACY — revised combined prompt (superseded)
```

## Companion Repository
Sensitive constants and secure data extraction utilities live in a **separate private repo**
(`llm_dup_pv_secure`) which will import this repo as a package. Do not add real data or 
credentials to this repo.

## Key Design Decisions
<!-- Fill in the decisions that aren't obvious from the code, e.g.:
- Why the decision tree is deterministic while the LLM steps are flexible
- How to handle FAERS vs VigiBase differences
-->

## Data Flow
1. **Input**: Two report IDs
2. **Fetch**: `make_report_from_report_id(report_id, Target_DB)` → `Report` objects
3. **Enrich** (optional): `fetch_meta()` → literature/study metadata via external APIs
4. **LLM calls** (4 parallel assessments): same-patient · AE-similarity · clustering · literature
5. **Classify**: `classify_pair(literature_json, ae_json, clustering_json, same_patient_json)` → label + explanation
6. **Output**: HTML report with colour-coded classification card and demographics comparison

## LLM Integration Conventions
- All LLM responses are structured JSON; use `util.extract_json_from_GPTresponse()` to parse
- Each prompt file in `vigiMatchContext/` is self-contained; load with `util.read_file()`
- Schemas:
  - same_patient:
      `{"same_individual_assessment": "same|different|unclear", "motivation": "..."}`
  - ae_similarity:
      `{"ae_summary": "...", "ae_consistency": "inconsistent|partially_consistent|consistent"}`
  - clustering:
      `{"cluster_assessment": "no_evidence_of_cluster|possible_cluster", "motivation": "...",
        "cluster_description": "...", "cluster_hypotheses": "..."}`
  - literature:
      `{"Single_patient_study": "True|False|unclear", "Multi_patient_study": "True|False|unclear",
        "same_study_cohort": "True|False|unclear", "motivation": "..."}`
  - decision_logic (experimental, prompt_DecisionLogic.txt):
      `{"Decision": "Duplicate|Related|Non-Duplicate", "motivation": "..."}`

## Environment Variables Required
```
UNPAYWALL_EMAIL=          # For Unpaywall open-access API
NCBI_TOOL=                # Tool name for NCBI E-utilities
NCBI_EMAIL=               # Email for NCBI queries
NCBI_API_KEY=             # Optional; increases NCBI rate limit
FETCH_STUDY_HIGH_CONFIDENCE=0.85   # Minimum confidence to accept a study hit
```
DB connection details live in the secure companion repo.

## Dependencies
- `umc-research` — proprietary UMC library for DB access and MedDRA codes
- `beautifulsoup4` — HTML parsing for PDF discovery
- `pandas`, `sqlalchemy`, `requests`, `joblib`, `dateutil` — standard stack

## Conventions
- Python ≥3.11, <3.13
- Package installed as `llm_dup_dev` (src layout via pyproject.toml)
- Data files (`.csv`, `.parquet`, `.pkl`, `.json`, `.xlsx`) should not be saved 
in this repo and as an extra precaution are gitignored — never commit them

## What This Repo Is NOT
- No UI layer — HTML snippets are embedded in a larger calling system
- No test suite here — tests live elsewhere
- No real data or sensitive information — all credentials are in environment variables or the secure companion repo
