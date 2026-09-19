# vigiTwin

A **duplicate adjudication pipeline** for pharmacovigilance adverse event
reports, plus the evaluation framework used to measure its performance.

> **On names.** The method is **vigiTwin**. The Python package is still
> `llm_dup_dev`, the working name it carried before the method was named.
> Renaming the package changes two JSON schemas and every import in the
> companion repository, so it is deliberately held back to 2.0.0 where it can
> ship with a migration note — see [`CHANGELOG.md`](CHANGELOG.md). Until then:
> the repository is `vigiTwin`, the import is `llm_dup_dev`.

Report pairs that an upstream candidate-generation method (e.g. [vigiMatch
2025](https://doi.org/10.48550/arXiv.2504.03729)) has flagged as suspected
duplicates are passed to a transparent, rule-based decision logic that
classifies each pair as **Duplicates**, **Otherwise related**, **Unrelated**, or
**Abstain**. The decision logic is deterministic; language models are used only
to extract structured evidence from free-text narratives, never to make the
final call.

This repository contains the method, the data models, the JSON schemas, and the
evaluation package. It contains **no data and no database access** — see
[Two repositories](#two-repositories) below.

## What the method does

The pipeline combines four kinds of evidence about a report pair:

1. **Rarity-based matching evidence** — how unlikely it would be for two
   unrelated reports to share the observed combination of clinical features,
   evaluated against a comparison population defined by the pair's shared
   reporting context.
2. **Inconsistency signals** from structured fields and clinical dates —
   information arguing *against* the duplicate hypothesis.
3. **Narrative-derived signals** — adverse-event clinical picture (with a
   deterministic MedDRA-hierarchy fallback), plus per-report source/case and
   demographics extraction.
4. **Explicit inter-report links** — derived deterministically from the reports'
   structured case identifiers (ICH E2B(R3) C.1.9).

These feed a two-step decision: an **independence assessment** (could these two
reports plausibly be independent draws from the comparison population?) and,
for non-independent pairs, a **relationship assessment** (same case, different
case, or insufficient evidence). The pair of internal answers maps to the four
user-facing labels above.

Full detail: [`docs/duplicate_adjudication_spec.md`](docs/duplicate_adjudication_spec.md).
Shorter tour: [`docs/pipeline_overview.md`](docs/pipeline_overview.md).

## Install

```bash
pip install git+https://github.com/Uppsala-Monitoring-Centre/vigiTwin.git@v1.0.0
```

Python 3.11 or 3.12. The package is pure Python with four dependencies
(`pydantic`, `jinja2`, `pyyaml`, `openpyxl`) and installs anywhere. It is not
on PyPI yet; a PyPI release is planned to accompany the package rename in
2.0.0.

## What you can run without database access

Everything in this repository is developed and unit-tested against **synthetic
data**, so the full test suite runs on a clean install:

```bash
pip install -e ".[dev]" && python -m pytest
```

`llm_dup_dev.study_eval` — the performance-evaluation package, including
sampling, precision/recall estimation, and the two-reviewer structured
case-level examination (SCLE) with arbitration — is entirely deterministic and
runs end-to-end on synthetic input. See [`docs/study_eval.md`](docs/study_eval.md)
for a worked four-step example you can execute as written.

What you **cannot** run here is the pipeline against real reports. That needs a
pharmacovigilance database and an LLM deployment, both of which live on the
secure side.

## Two repositories

| | `vigiTwin` (this repo) | `llm_dup_pv_secure` (private) |
|---|---|---|
| **Holds** | Models, feature generation, decision logic, evaluation framework, schemas | Database access, real data, LLM execution |
| **Defines** | `Report`, `ReportPair`, `PairFeatures`, `PipelineRunner`, `ClassificationResult`, `EvalSummary` | Concrete `ReportLoader` and `StatsProvider` implementations |

The secure repository installs this one as a dependency and supplies the two
protocol implementations that connect the pipeline to real data. No real data,
credentials, or database connection strings exist in this repository or anywhere
in its history.

Integration detail, including the offline "frozen validation" mode used on
locked-down compute: [`docs/integration_guide.md`](docs/integration_guide.md).
Rationale for the split: [`docs/repository_roles.md`](docs/repository_roles.md).

## Repository layout

```
docs/        Method specification, pipeline overview, integration guide,
             data contracts, and per-change migration notes
schemas/     JSON Schema for every artifact the pipeline emits
examples/    A valid example document for each schema
src/         The package (see docs/integration_guide.md § Public surface)
tests/       ~1800 tests, all on synthetic data
```

## Versioning and provenance

The version number is a **consumer contract** signal — the JSON schemas plus the
public API — not a per-commit identifier. Every artifact the pipeline emits
records both `dev_package.version` (what the contract was) and `dev_git_commit`
(exactly which code ran). See [`CHANGELOG.md`](CHANGELOG.md) for the narrative of
what changed for consumers at each version, and `agents.md` § Conventions →
Versioning for the bump rules.

## Citation

Citation metadata is in [`CITATION.cff`](CITATION.cff).

<!-- TODO before publication: uncomment `doi` in CITATION.cff and add the
     Zenodo concept-DOI badge here once the first release is archived. -->

## License

Apache License 2.0 — see [`LICENSE`](LICENSE). Copyright 2026 Uppsala
Monitoring Centre.

Apache-2.0 was chosen over a shorter permissive licence for its explicit patent
grant and its explicit non-grant of trademark rights: the licence permits use,
modification and redistribution, including commercially, but confers no right
to use the names or marks of Uppsala Monitoring Centre, or to imply its
endorsement of a modified version.


