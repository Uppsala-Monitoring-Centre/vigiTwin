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
│   ├── evidence_format.md                  # The reader-facing `evidence` review
│   ├── evidence_publication_mode.md        # Scrambling + fail-closed export
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
    │   └── evidence/                  #   reader-facing evidence review
    │       └── publication/           #     scrambling for publication
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
VIGITWIN_PUBLICATION_SALT=         # Salt for publication-mode scrambling;
                                   #   no default, from the secure key store
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

### Checks
Run all three before opening a PR. They are configured in `pyproject.toml` and
run in CI (`.github/workflows/ci.yml`):

```
.venv/bin/python -m pytest      # blocking in CI — must stay green
.venv/bin/ruff check src tests  # blocking in CI — must stay at zero
.venv/bin/mypy                  # blocking in CI — must stay at zero
```

- **All three block.** 2031 tests, all on synthetic data, no network or
  database; zero ruff findings; zero mypy errors. A PR that reddens any of them
  does not merge.
- **Zero is the baseline, so a finding is a regression.** Through 1.0.0 ruff and
  mypy were advisory and carried 36 and 33 inherited findings, because 1.0.0
  deliberately changed no `src/`. Those were cleared in 1.0.1 and both checks
  turned blocking in the same change — an advisory check nobody has to satisfy
  stops being a check.
- **Suppress only with a reason.** A `# noqa: <code>` or `# type: ignore[<code>]`
  needs a comment saying why, and must name the specific code — a bare
  suppression hides the next defect in that line. Where an invariant is real but
  not expressible in the type, prefer a guard or an `assert` that would fail
  loudly, over a silent cast.
- **The rule selection is narrow on purpose**, and pinned explicitly rather than
  left to the tool default, so a check here, in CI, and on a contributor's clone
  agree regardless of installed version. It selects rules that find defects, not
  rules that express formatting preferences — see the comment in
  `pyproject.toml` for what was excluded and why.

### Versioning
- The single source of truth is `version` in `pyproject.toml`; it is
  consumed at runtime (baked into manifests as `dev_package.version`).
- **This is not strict SemVer, deliberately.** The version signals how
  *significant* a change is, not what shape of diff produced it. A
  renamed field does not spend a major; a new generation of the method
  does. Read a version number here as "how far the method has moved",
  not as a machine-checkable compatibility promise — which is why an
  incompatible change is *documented* rather than merely numbered (see
  below).
- **Exact identity vs. significance are split across two manifest
  fields.** `dev_git_commit` records *which precise code ran* (unique,
  automatic). `dev_package.version` records *how far the method has
  come* — it is deliberately sticky. Don't couple the version to git;
  the commit already identifies the code.
- Size the bump by what the change means to someone using the method:
  - **major** (`2.3.4` → `3.0.0`) — a substantial new generation of
    vigiTwin: a methodological advance intended for sustained use, and
    potentially for its own scientific evaluation. **Breaking API or
    schema changes alone do not warrant a major bump**, however many
    names move.
  - **minor** (`2.3.4` → `2.4.0`) — meaningful new functionality or a
    substantial enhancement; the evidence presentation added in 1.1.0
    is the worked example. May carry backward-incompatible API or
    schema changes, including package restructuring or renaming.
  - **patch** (`2.3.4` → `2.3.5`) — bug fixes, smaller functional
    improvements, and minor interface or schema adjustments, including
    limited backward-incompatible ones.
  - **no bump** — documentation, tests, refactoring, and anything else
    with no meaningful functional impact.
- **Nothing bumps automatically.** A touched `schemas/*.schema.json`,
  or a moved name in `docs/integration_guide.md` § Public surface, is a
  prompt to ask how significant the change is — not a trigger. The
  previous rule bumped on contract shape alone and spent eleven version
  numbers on three days of work, twice having to argue in the
  `CHANGELOG` against the rule it was following; see
  `docs/migration_notes_version_renumbering.md`.
- **An incompatible change is documented whether or not it moves the
  version.** Decoupling the number from the break puts the entire
  warning on the prose: say in `CHANGELOG.md` what moved and how to
  update, and add a `docs/migration_notes_*.md` wherever a consumer
  needs steps rather than a sentence. A break nobody wrote down is the
  one failure mode this convention can produce, and the note is what
  prevents it.
- **The contract is still worth naming**, even though it no longer
  drives the number: the JSON schemas in `schemas/` plus the public
  surface enumerated in `docs/integration_guide.md` § Public surface.
  Anything not listed there is internal and may change in any release.
- **No deprecation window.** With a single known consumer, a breaking
  change ships with a migration note, not a two-release deprecation
  cycle. Say what moved and how to update.
- **Releases and study cycles are different things.** A completed study
  cycle is marked by a git tag and an archived snapshot (DOI), which is
  what a publication cites — do not reach for a major bump to signal
  "this is the version behind the paper".
- Record every bump in `CHANGELOG.md` under a new version heading —
  that file carries the "what changed and why it mattered" narrative
  that neither the SHA nor the version number expresses on its own. A
  change that takes no bump still belongs there when a consumer would
  notice it; it goes under `[Unreleased]`.

### Tags, GitHub Releases and DOIs
Three separate things, deliberately decoupled — conflating them is how a
provisional commit acquires a permanent citation:

| | What it is | Cost |
|---|---|---|
| `version` in `pyproject.toml` | how far the method has moved | spent the moment any artifact records it |
| a git tag | an immutable name for a commit | free; in practice irrevocable once public |
| a GitHub **Release** | what Zenodo watches | mints a permanent version DOI, listed forever on the concept record |

- **`origin` is where work happens; `github` mirrors selected validated
  functionality, not `main`.** Development, PRs and review all run on Azure
  DevOps. A version reaches
  <https://github.com/Uppsala-Monitoring-Centre/vigiTwin> only when two
  things hold: it has been **exercised against the secure companion repo**,
  and there is **confidence that it is stable and of value to the broader
  community**. The first is necessary and not sufficient. A version that has
  merely run secure-side is *eligible*, not ready, and eligibility is not a
  reason to publish.
- **A backlog of unmirrored versions on `main` is the normal state, not a
  debt to clear.** Most versions never become a public milestone of their
  own: several of them collapse into one, which is one push, one tag and
  one README pin. Publishing the backlog because it has accumulated would
  make the mirror the replay of every merge this policy exists to prevent.
  Do not propose mirroring; the decision is the maintainer's, and it turns
  on the two conditions above rather than on how many versions have gone by.
- **A milestone carries the full intervening commit history.** What is
  selected is *which version* becomes public, not which commits. The push
  fast-forwards `github/main`, so every commit between the last public tag
  and the new one goes out with it — nothing is squashed, rebased or
  flattened on the way. That is what keeps `github/main` an ancestor of
  `origin/main`, and it is also what makes the line between the two
  histories usable: everything up to the public tip is permanent, and
  everything after it is still ours to rewrite.
- **Tag every version that reaches GitHub.** A tag costs nothing and buys
  three things: `git describe` resolves on any public commit, the README's
  install-from-tag instruction has a tag to point at for every public
  version, and the contract state of each public commit is addressable by
  name instead of by reading `pyproject.toml` at that SHA. The failure mode to
  avoid is a public commit with user-visible changes and no name to cite it
  by. Tag `v<version>`, annotated, on the commit that is pushed, and move the
  README's pinned install example to the new tag in the same change — an
  install line pointing at a superseded version is the one piece of drift a
  reader follows by default.
- **Cut a Release only for significant, stable, citable versions**: one
  referenced in a manuscript, or one whose changes external users should be
  actively retrieving. Everything between those gets a tag and no Release. Zenodo
  mints a DOI from a Release, never from a tag push, so this is the one step
  that cannot be taken back or quietly corrected.
- **A Release and `CITATION.cff` move in the same change, and only then.**
  `CITATION.cff` describes the last *archived* release, not the newest tag,
  so bump `version:` and `date-released:` there when cutting a Release — and
  leave them alone when a version is merely tagged, or the file claims an
  archive that does not exist. The `doi:` field holds the *concept* DOI (all
  versions); each Release gets its own version DOI underneath it. Say in the
  tag message which case applies, as the `v1.0.1` and `v1.0.2` messages do:
  it is the only place a reader of a bare tag can learn whether it was
  archived.
- **A study cycle gets its own tag**, named for the study rather than the
  version (`study-2026-performance-evaluation`), pointing at the version tag
  that was used. It names the analysis, not the contract — see § Versioning
  on why a study cycle is not a release.

## What This Repo Is NOT
- No UI layer — HTML snippets are embedded in a larger calling system
- No real data or sensitive information — all credentials are in environment variables or the secure companion repo
