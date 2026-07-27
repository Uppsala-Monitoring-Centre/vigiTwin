# AI Agent Implementation Guide

This repository is designed to be developed with assistance from AI coding agents.

## Purpose of repo
A **duplicate adjudication pipeline** (with optional LLM-assisted evidence
extraction) for pharmacovigilance adverse event reports (VigiBase/FAERS).
Report pairs that have been flagged as suspected duplicates by the vigiMatch
2025 method (see: https://doi.org/10.48550/arXiv.2504.03729) are passed to a
transparent rule-based decision logic that classifies each pair as
**Duplicates**, **Otherwise_related**, **Unrelated**, or **Abstain**. The
pipeline combines probabilistic match signals, structured/clinical-date
inconsistency signals, and (planned) LLM-based narrative consistency
assessment.

Historically the pipeline was framed as a "same-individual assessment".
The current framing is **report-pair adjudication / duplicate
adjudication**, and the rarity-based matching component is treated as a
test of the **independence null** (was it plausible that the two reports
were independent draws from the comparison population?). The decision
logic answers two questions in sequence — an `IndependenceAssessment`
(`Non-independent / Independent`) and, for non-independent pairs, a
`RelationshipAssessment` (`Same_case / Different_case / Abstain`) —
which `ClassificationResult.final_label` maps to the user-facing
`Duplicates / Otherwise_related / Unrelated / Abstain` labels. See
`docs/duplicate_adjudication_spec.md` § Final output for the full
mapping and rationale.

## Core principles

Existing scripts should be treated as **reference implementations**, not as code to reuse.

The new implementation should be:

- modular
- typed
- testable
- easy to evaluate experimentally

Avoid copying legacy scripts directly.

Prefer explicit data models (dataclasses or pydantic) rather than passing raw dictionaries.

## Architecture goals

The system should consist of clear modules:

- data models
- feature extraction
- probability estimation
- LLM reasoning components
- pipeline orchestration

Specifications live in /docs
Code lives in /src
Tests live in /tests

## Data access

AI agents must **not introduce direct database connections** unless explicitly specified.

Data extraction scripts may exist in separate secure repositories.

## Testing expectations

Prefer small, testable functions.

Unit tests should be added for key components where possible.

## Specification source

Implementation should follow the specifications in:

`docs/duplicate_adjudication_spec.md`

If specifications conflict with legacy code, **the specification takes precedence.**

## Repository Structure
```
./
├── agents.md
├── README.md
├── main.py
├── pyproject.toml
│
├── docs/
│   ├── data_contracts.md                   # Data layer schemas and contracts
│   ├── pipeline_overview.md                # End-to-end pipeline description
│   ├── repository_roles.md                 # Repo separation and security controls
│   └── duplicate_adjudication_spec.md      # Primary method specification
│
├── examples/
│   ├── pair_features_example.json
│   ├── report_example.json
│   └── report_pair_example.json
│
├── schemas/
│   ├── pair_features.schema.json
│   ├── report.schema.json
│   └── report_pair.schema.json
│
└── src/llm_dup_dev/
    ├── classifier/                    # (planned) Duplicate classification logic
    ├── data_extraction/               # (planned) Report extraction and structuring
    ├── feature_generation/            # (planned) PairFeatures computation
    ├── narrative_analysis/            # (planned) LLM-based narrative assessment
    ├── prompts/                       # (planned) LLM prompt templates
    │
    ├── eval/                          # Evaluation framework (imported by secure repo)
    │   ├── labels.py                  # Canonical label constants
    │   ├── metrics.py                 # ConfusionMatrix, accuracy functions, EvalSummary
    │   ├── comparison.py              # evaluate() — main entry point
    │   └── report.py                  # Text/markdown summary formatting
    │
    ├── lib/                           # LEGACY reference implementations
    │   ├── data_extraction.py         # LEGACY - Fetches & structures individual reports from SQL Server
    │   ├── data_extraction_demographics.py  # LEGACY - Aggregate demographic profiles (joblib-cached SQL CTEs)
    │   ├── decision_logic.py          # LEGACY - Deterministic classification decision tree
    │   ├── fetch_study.py             # LEGACY - Multi-source literature lookup (PubMed/Crossref/EuropePMC)
    │   ├── html_rendering_functions.py  # LEGACY - HTML output generation
    │   └── util.py                    # LEGACY - Shared helpers (JSON parsing, missing-value checks)
    │
    └── vigiMatchContext/              # LEGACY - LLM system prompts and annotation guidelines
        ├── prompt_system.txt          # LEGACY - LLM role definition
        ├── prompt_same_patient.txt    # LEGACY - Same-patient assessment prompt
        ├── prompt_AE_similarity.txt   # LEGACY - Adverse-event similarity prompt
        ├── prompt_clustering.txt      # LEGACY - Cluster-detection prompt
        ├── prompt_DecisionLogic.txt   # LEGACY - LLM-based final classification (not production)
        ├── prompt_literature.txt      # LEGACY - Literature/study evaluation prompt
        ├── context01.txt              # Full vigiMatch annotation guidelines
        ├── context_nn1.txt            # LEGACY — combined drug+AE prompt (superseded)
        └── context_nn1b.txt           # LEGACY — revised combined prompt (superseded)
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

### Versioning
- The single source of truth is `version` in `pyproject.toml`; it is
  SemVer and consumed at runtime (baked into manifests as
  `dev_package.version`).
- **Exact identity vs. semantic meaning are split across two manifest
  fields.** `dev_git_commit` records *which precise code ran* (unique,
  automatic). `dev_package.version` records *the state of the consumer
  contract* — it is deliberately sticky and changes only when that
  contract changes. Don't couple the version to git; the commit already
  identifies the code.
- **The contract = the JSON schemas + the public API the secure repo
  imports.** Bump the version in the *same PR* that changes any
  `schemas/*.schema.json` or a public API shape:
  - pre-1.0: breaking change (renamed/removed field, changed manifest
    shape or label semantics) → minor (`0.2.0` → `0.3.0`); additive or
    backward-compatible change / fix → patch (`0.2.0` → `0.2.1`).
  - reserve `1.0.0` for a deliberately-declared stable schema/API.
- Record every bump in `CHANGELOG.md` under a new version heading —
  that file carries the "what changed for consumers" narrative that
  neither the SHA nor the version number expresses on its own.

## What This Repo Is NOT
- No UI layer — HTML snippets are embedded in a larger calling system
- No real data or sensitive information — all credentials are in environment variables or the secure companion repo
