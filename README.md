# vigiTwin

[![CI](https://github.com/Uppsala-Monitoring-Centre/vigiTwin/actions/workflows/ci.yml/badge.svg)](https://github.com/Uppsala-Monitoring-Centre/vigiTwin/actions/workflows/ci.yml)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.24198670.svg)](https://doi.org/10.5281/zenodo.24198670)

A **duplicate adjudication pipeline** for pharmacovigilance adverse event
reports, plus the evaluation framework used to measure its performance.

> **On names.** The method, the repository and the package are all
> **vigiTwin** — the distribution is `vigitwin` and so is the import path,
> with no hyphen/underscore split to remember. Through 1.2.7 the
> package was `llm_dup_dev`, the working name it carried before the method was
> named; 1.3.0 renamed it. Artifacts written by those earlier versions record
> `dev_package.name: "llm-dup-dev"` and still correctly name the release that
> produced them. Migrating a consumer takes a few steps — see
> [`docs/migration_notes_package_rename.md`](https://github.com/Uppsala-Monitoring-Centre/vigiTwin/blob/main/docs/migration_notes_package_rename.md).

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

Full detail: [`docs/duplicate_adjudication_spec.md`](https://github.com/Uppsala-Monitoring-Centre/vigiTwin/blob/main/docs/duplicate_adjudication_spec.md).
Shorter tour: [`docs/pipeline_overview.md`](https://github.com/Uppsala-Monitoring-Centre/vigiTwin/blob/main/docs/pipeline_overview.md).

## Install

```bash
pip install git+https://github.com/Uppsala-Monitoring-Centre/vigiTwin.git@v1.0.2
```

Python 3.11 or 3.12. The package is pure Python with four dependencies
(`pydantic`, `jinja2`, `pyyaml`, `openpyxl`) and installs anywhere.

**Not yet on PyPI.** The project is packaged and ready for it as of 1.3.0 —
metadata, licence files, a typing marker and a trusted-publishing release
workflow are all in place — but nothing has been uploaded. The first upload
follows a version being exercised against the secure companion repository and
mirrored here, as `agents.md` § Tags, GitHub Releases and DOIs requires. Until
then, install from a tag; this line and the pinned tag above both change in
the same release that publishes.

## What you can run without database access

Everything in this repository is developed and unit-tested against **synthetic
data**, so the full test suite runs on a clean install:

```bash
pip install -e ".[dev]" && python -m pytest
```

`vigitwin.study_eval` — the performance-evaluation package, including
sampling, precision/recall estimation, and the two-reviewer structured
case-level examination (SCLE) with arbitration — is entirely deterministic and
runs end-to-end on synthetic input. To watch all four steps execute:

```bash
python scripts/study_eval_demo.py
```

The script builds its own synthetic reference and prediction sets, and fills
the reviewer and arbitration workbooks that a human would fill in a real study,
so the whole chain runs unattended. [`docs/study_eval.md`](https://github.com/Uppsala-Monitoring-Centre/vigiTwin/blob/main/docs/study_eval.md)
explains what each step does; the script is the executable version of it.

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
locked-down compute: [`docs/integration_guide.md`](https://github.com/Uppsala-Monitoring-Centre/vigiTwin/blob/main/docs/integration_guide.md).
Rationale for the split: [`docs/repository_roles.md`](https://github.com/Uppsala-Monitoring-Centre/vigiTwin/blob/main/docs/repository_roles.md).

## Repository layout

```
docs/        Method specification, pipeline overview, integration guide,
             data contracts, the evidence review format, and per-change
             migration notes
schemas/     JSON Schema for every artifact the pipeline emits
examples/    A valid example document for each schema
src/         The package (see docs/integration_guide.md § Public surface)
tests/       ~2000 tests, all on synthetic data
```

## Versioning and provenance

The version number is a **significance** signal — how far the method has moved
— and deliberately **not strict SemVer**: a major is reserved for a substantial
new generation of vigiTwin, a minor marks meaningful new functionality, and a
patch covers fixes and smaller adjustments. Breaking API or schema changes
alone do not earn a major, so an incompatible change is announced in
[`CHANGELOG.md`](https://github.com/Uppsala-Monitoring-Centre/vigiTwin/blob/main/CHANGELOG.md) — with a `docs/migration_notes_*.md` where
updating takes steps — rather than signalled by the number alone.

The version is never a per-commit identifier. Every artifact the pipeline emits
records both `dev_package.version` (how far the method had come) and
`dev_git_commit` (exactly which code ran). See `agents.md` § Conventions →
Versioning for the full convention.

## Citation

Every GitHub Release is archived on Zenodo. Citation metadata — including
author ORCID and the DOI — is in [`CITATION.cff`](https://github.com/Uppsala-Monitoring-Centre/vigiTwin/blob/main/CITATION.cff), which GitHub
reads to populate the *Cite this repository* sidebar.

Cite the **concept DOI** when you mean the software in general; it always
resolves to the latest release:

> [10.5281/zenodo.24198670](https://doi.org/10.5281/zenodo.24198670)

Cite the **version DOI** when reproducing a specific result, so the reader gets
the exact code that produced it. For v1.0.0 that is
[10.5281/zenodo.24198671](https://doi.org/10.5281/zenodo.24198671); later
releases each mint their own, listed on the Zenodo record.

The code behind the 2026 performance-evaluation study is additionally tagged
`study-2026-performance-evaluation`, which points at `v1.0.0`.

That study was pre-registered. Cite the protocol alongside the software when
reporting results from it:

> [10.17605/OSF.IO/HVTFS](https://doi.org/10.17605/OSF.IO/HVTFS)

## License

Apache License 2.0 — see [`LICENSE`](LICENSE). Copyright 2026 Uppsala
Monitoring Centre.

Apache-2.0 was chosen over a shorter permissive licence for its explicit patent
grant and its explicit non-grant of trademark rights: the licence permits use,
modification and redistribution, including commercially, but confers no right
to use the names or marks of Uppsala Monitoring Centre, or to imply its
endorsement of a modified version.


