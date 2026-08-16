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
inconsistency signals, and LLM-based narrative-derived signals (AE
clinical picture, narrative source/case, narrative demographics).

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
│   ├── duplicate_adjudication_spec.md      # Primary method specification
│   ├── pipeline_overview.md                # End-to-end pipeline description
│   ├── data_contracts.md                   # Data layer schemas and contracts
│   ├── integration_guide.md                # Running the pipeline; public surface
│   ├── repository_roles.md                 # Repo separation and security controls
│   ├── study_eval.md                       # Performance-evaluation package
│   ├── reference_set_characteristics.md    # "Table 1" for reference sets
│   └── migration_notes_*.md                # One per breaking contract change
│
├── examples/                          # One valid document per schema
├── schemas/                           # JSON Schema for every emitted artifact
│
└── src/llm_dup_dev/
    ├── models/                        # Report, ReportPair, PairFeatures, results
    ├── data/                          # ReportLoader / StatsProvider protocols;
    │                                  #   synthetic + offline (frozen) providers
    ├── feature_generation/            # PairFeatures from two Reports + stats
    ├── classifier/                    # Decision logic and configurable thresholds
    ├── llm/                           # LLM boundary: protocols, models, fake client
    │   └── tasks/                     # Prompt construction + typed extractions
    ├── pipeline/                      # runner.py (main entry point), export.py
    │
    ├── eval/                          # Evaluation framework (imported by secure repo)
    ├── study_eval/                    # Study workflow: sampling, estimation,
    │   └── scle/                      #   two-reviewer SCLE + arbitration workbooks
    ├── characterization/              # Descriptive "Table 1" for reference sets
    ├── review_html/                   # Side-by-side HTML review renderer
    │
    ├── dataset_manifest/              # Per-validation-set manifest
    └── run_manifest/                  # Per-run manifest (version + git provenance)
```

The legacy reference implementations (`lib/`, `vigiMatchContext/`) have been
removed; consult git history if an original is needed.

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
This package is public and must stay installable by anyone: **no proprietary or
internal-index dependency may be added here.** Anything needing `umc-research`,
a database driver, or an Azure client belongs in the secure companion repo.

- `pydantic` — all data models
- `jinja2` — HTML review rendering
- `pyyaml` — manifest writers
- `openpyxl` — SCLE reviewer/arbitration workbooks (core, not an extra:
  `study_eval` imports it at module level and is a documented entry point)

Keep the list short and justified; each addition is a cost to every consumer.

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
- **The contract = the JSON schemas + the public surface enumerated in
  `docs/integration_guide.md` § Public surface.** Anything not listed
  there is internal and may change in any release. Bump the version in
  the *same PR* that changes any `schemas/*.schema.json` or any listed
  name:
  - breaking change (renamed/removed field or name, changed manifest
    shape or label semantics) → **major** (`1.4.2` → `2.0.0`)
  - additive, backward-compatible change → **minor** (`1.4.2` → `1.5.0`)
  - fix with no contract change → **patch** (`1.4.2` → `1.4.3`); this
    applies to behaviour fixes too, even where no schema or API shape
    moves — the fix changes what a consumer gets back, so it gets its
    own version and its own CHANGELOG heading
  - (pre-1.0 the rule was breaking → minor, everything else → patch;
    that is history now, retained only to read old entries.)
- **No deprecation window.** With a single known consumer, a breaking
  change ships as a major bump plus a `docs/migration_notes_*.md`, not
  as a two-release deprecation cycle. Say what moved and how to update.
- **Releases and study cycles are different things.** The version tracks
  compatibility only. A completed study cycle is marked by a git tag and
  an archived snapshot (DOI), which is what a publication cites — do not
  reach for a major bump to signal "this is the version behind the
  paper".
- Record every bump in `CHANGELOG.md` under a new version heading —
  that file carries the "what changed for consumers" narrative that
  neither the SHA nor the version number expresses on its own.

## What This Repo Is NOT
- No UI layer — HTML snippets are embedded in a larger calling system
- No real data or sensitive information — all credentials are in environment variables or the secure companion repo
