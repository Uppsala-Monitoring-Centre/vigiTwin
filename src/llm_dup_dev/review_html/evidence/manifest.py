"""A small, validated manifest for curating a collection of examples.

Distinct from ``dataset_manifest`` and ``run_manifest``, which record what a
*run* was. This records an editorial choice: which pairs to show, in what
order, with what framing — the kind of thing a person writes by hand and
revises.

What it deliberately does **not** carry is any value the artifacts already
hold. Reference labels, screening outcomes, classifications and evidence are
read from the saved run, never copied here, so the manifest cannot drift out
of step with the run it describes. ``extra="forbid"`` is what enforces that:
a stray ``reference_label:`` key is a hard error rather than a field quietly
ignored while the author believes it is in effect.

Format: YAML by default, JSON accepted, chosen by file suffix. YAML because
this is the one artifact in the package a human writes and re-edits, and
multi-paragraph case interpretations are its main content — JSON has no block
scalars and no comments. Read with ``yaml.safe_load`` only, and every field is
validated by a strict Pydantic type, which also neutralises YAML's implicit
coercions (an unquoted country code ``NO`` becoming ``False``, say).
"""

from __future__ import annotations

import json
from collections import Counter
from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from llm_dup_dev.study_eval.scle.form import ReferenceLabelIssue

from ..render import _safe_filename
from .build import SCLEState

SCHEMA_VERSION = "1.0"


class SummaryMode(str, Enum):
    """Which prose block an example shows."""

    #: The generated account of the assessment.
    DETERMINISTIC = "deterministic"
    #: A human's reading, in place of the generated account. The default when
    #: an interpretation is supplied: showing both says the same thing twice
    #: and leaves a reader unsure which carries authority.
    INTERPRETATION = "interpretation"
    #: Both, the human's first.
    BOTH = "both"


class ReviewStatus(str, Enum):
    """How far an example has got through review."""

    DRAFT = "draft"
    AUTHOR_REVIEW = "author_review"
    APPROVED = "approved"


class SCLEAssessmentEntry(BaseModel):
    """An SCLE verdict on the reference label, or a stated absence of one.

    The four states are not interchangeable, and a reader of the finished
    document must be able to tell them apart: nobody has assessed this yet;
    this pair was deliberately left unassessed; it was assessed; it was
    assessed and found genuinely ambiguous.
    """

    model_config = ConfigDict(extra="forbid")

    state: SCLEState = SCLEState.NOT_ENTERED
    issue: ReferenceLabelIssue | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _check_state_and_issue(self) -> "SCLEAssessmentEntry":
        if self.state is SCLEState.ASSESSED:
            if self.issue is None:
                raise ValueError(
                    "scle_assessment state 'assessed' needs an issue "
                    "(no_apparent_issue or possibly_incorrect)."
                )
            if self.issue is ReferenceLabelIssue.GENUINELY_AMBIGUOUS:
                raise ValueError(
                    "issue 'genuinely_ambiguous' is the 'inconclusive' state; "
                    "set state: inconclusive rather than state: assessed."
                )
        elif self.state is SCLEState.INCONCLUSIVE:
            if self.issue not in (None, ReferenceLabelIssue.GENUINELY_AMBIGUOUS):
                raise ValueError(
                    "scle_assessment state 'inconclusive' carries issue "
                    "'genuinely_ambiguous' or none at all; "
                    f"got {self.issue.value!r}."
                )
            self.issue = ReferenceLabelIssue.GENUINELY_AMBIGUOUS
        elif self.issue is not None:
            raise ValueError(
                f"scle_assessment state {self.state.value!r} cannot carry an "
                "issue: no assessment was made."
            )
        return self


class ExampleDataset(BaseModel):
    """One saved run, and where its artifacts are.

    The artifact combination mirrors ``load_saved_run``'s own precedence
    exactly — a combined ``pair_results`` file, or both split files — so a
    manifest that validates here cannot fail later for a reason the author
    could have been told about up front.
    """

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    reports: Path
    pairs: Path
    pair_results: Path | None = None
    features: Path | None = None
    classifications: Path | None = None
    pair_frequencies: Path | None = None
    screening: Path | None = None
    annotations: Path | None = None
    narrative_demographics: Path | None = None
    #: The run manifest, read for the thresholds and the numerical stabiliser
    #: the run used. Without it the reviews name evidence zones but quote no
    #: thresholds, and cannot state the score ceiling.
    run_manifest: Path | None = None
    #: The key the screening artifact stores its own verdict under, when it
    #: is not one of the names :mod:`.screening` already knows. Per dataset,
    #: because a collection may draw on exports from different producers.
    screening_verdict_key: str | None = None
    #: Per dataset, never global: a collection spanning several reference sets
    #: must not relax identity validation everywhere because one old set
    #: needs it.
    allow_noncanonical_pairs: bool = False

    @model_validator(mode="after")
    def _check_artifacts(self) -> "ExampleDataset":
        has_split = self.features is not None and self.classifications is not None
        if self.pair_results is None and not has_split:
            raise ValueError(
                "a dataset needs either 'pair_results', or both 'features' "
                "and 'classifications'."
            )
        if self.pair_results is not None and (
            self.features is not None or self.classifications is not None
        ):
            raise ValueError(
                "a dataset carries either 'pair_results' or the split "
                "'features'/'classifications' pair, not both: which one would "
                "be used is not something a reader should have to guess."
            )
        return self


class CuratedExample(BaseModel):
    """One pair, with the framing a curator chose for it."""

    model_config = ConfigDict(extra="forbid")

    dataset: str
    pair_id: str
    #: Short label used as the filename stem, the section anchor and the
    #: reference in the surrounding text (``S1``, ``S2``, …).
    example_id: str | None = None
    title: str | None = None
    lede: str | None = None
    kicker: str | None = None
    scle_assessment: SCLEAssessmentEntry | None = None
    #: Human-authored. Never generated, never overwritten by regeneration —
    #: the generator has no write path to this file.
    case_interpretation: str | None = None
    summary_mode: SummaryMode | None = None
    review_status: ReviewStatus = ReviewStatus.DRAFT
    #: Curator's working notes. Never rendered.
    notes: str | None = None

    def resolved_summary_mode(self) -> SummaryMode:
        """An interpretation replaces the generated summary unless told not to."""
        if self.summary_mode is not None:
            return self.summary_mode
        if self.case_interpretation:
            return SummaryMode.INTERPRETATION
        return SummaryMode.DETERMINISTIC


class CuratedExampleManifest(BaseModel):
    """An ordered collection of examples drawn from one or more saved runs."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    title: str | None = None
    intro: str | None = None
    note: str | None = None
    #: Names the rule used to compare a reference label against vigiTwin's
    #: four-label output. A study choice, so it is stated rather than assumed.
    positive_class: str = "primary"
    datasets: dict[str, ExampleDataset]
    examples: list[CuratedExample]

    @model_validator(mode="after")
    def _check_collection(self) -> "CuratedExampleManifest":
        if not self.datasets:
            raise ValueError("a manifest needs at least one dataset.")
        if not self.examples:
            raise ValueError("a manifest needs at least one example.")

        unknown = sorted(
            {e.dataset for e in self.examples if e.dataset not in self.datasets}
        )
        if unknown:
            raise ValueError(
                f"example(s) name dataset(s) the manifest does not define: "
                f"{', '.join(unknown)}. Defined: "
                f"{', '.join(sorted(self.datasets))}."
            )

        id_counts = Counter(e.example_id for e in self.examples if e.example_id)
        duplicates = sorted(i for i, n in id_counts.items() if n > 1)
        if duplicates:
            raise ValueError(
                f"duplicate example_id(s): {', '.join(duplicates)}."
            )

        # Two ids differing only in punctuation collapse to one filename, and
        # the second would silently overwrite the first.
        by_filename: dict[str, list[str]] = {}
        for example in self.examples:
            stem = example.example_id or example.pair_id
            by_filename.setdefault(_safe_filename(stem), []).append(stem)
        collisions = {
            name: ids_ for name, ids_ in by_filename.items() if len(ids_) > 1
        }
        if collisions:
            detail = "; ".join(
                f"{', '.join(ids_)} -> {name}.html"
                for name, ids_ in sorted(collisions.items())
            )
            raise ValueError(
                f"example identifiers collide once reduced to filenames, so "
                f"one would overwrite another: {detail}."
            )

        pair_counts = Counter((e.dataset, e.pair_id) for e in self.examples)
        repeats = sorted(
            f"{dataset}/{pair_id}"
            for (dataset, pair_id), n in pair_counts.items()
            if n > 1
        )
        if repeats:
            raise ValueError(
                f"the same pair appears more than once: {', '.join(repeats)}."
            )
        return self


class LoadedExampleManifest(BaseModel):
    """A manifest plus the directory its relative paths resolve against."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    manifest: CuratedExampleManifest
    base_dir: Path
    path: Path

    def dataset_paths(self, dataset_id: str) -> dict[str, Path | bool]:
        """Resolved artifact paths for one dataset, as ``load_saved_run`` kwargs."""
        dataset = self.manifest.datasets[dataset_id]
        mapping: dict[str, Path | bool] = {
            "reports_path": self._resolve(dataset.reports),
            "pairs_path": self._resolve(dataset.pairs),
            "allow_noncanonical_pairs": dataset.allow_noncanonical_pairs,
        }
        optional = {
            "pair_results_path": dataset.pair_results,
            "features_path": dataset.features,
            "classifications_path": dataset.classifications,
            "pair_frequencies_path": dataset.pair_frequencies,
            "screening_path": dataset.screening,
            "annotations_path": dataset.annotations,
            "narrative_demographics_path": dataset.narrative_demographics,
        }
        for key, value in optional.items():
            if value is not None:
                mapping[key] = self._resolve(value)
        return mapping

    def screening_verdict_key(self, dataset_id: str) -> str | None:
        """The screening verdict key this dataset names, if any."""
        return self.manifest.datasets[dataset_id].screening_verdict_key

    def run_manifest_path(self, dataset_id: str) -> Path | None:
        value = self.manifest.datasets[dataset_id].run_manifest
        return None if value is None else self._resolve(value)

    def _resolve(self, path: Path) -> Path:
        return path if path.is_absolute() else (self.base_dir / path)


def _load_mapping(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        loaded = yaml.safe_load(text)
    else:
        loaded = json.loads(text)
    if not isinstance(loaded, dict):
        raise ValueError(
            f"{path}: expected a manifest object at the top level, got a "
            f"{type(loaded).__name__}."
        )
    return loaded


def load_curated_manifest(
    path: str | Path, *, check_artifacts: bool = True
) -> LoadedExampleManifest:
    """Read and validate a curated example manifest.

    Relative artifact paths resolve against the manifest's own directory, so a
    collection can be moved as a unit. When ``check_artifacts`` is set, every
    resolved path is checked for existence and *all* missing ones are reported
    in one error — a manifest with three stale paths should say so once, not
    over three edit-and-retry cycles.
    """
    manifest_path = Path(path)
    raw = _load_mapping(manifest_path)
    manifest = CuratedExampleManifest.model_validate(raw)
    loaded = LoadedExampleManifest(
        manifest=manifest,
        base_dir=manifest_path.parent,
        path=manifest_path,
    )

    if check_artifacts:
        missing: list[str] = []
        for dataset_id in manifest.datasets:
            paths = dict(loaded.dataset_paths(dataset_id))
            run_manifest = loaded.run_manifest_path(dataset_id)
            if run_manifest is not None:
                paths["run_manifest"] = run_manifest
            for key, value in paths.items():
                if isinstance(value, Path) and not value.exists():
                    missing.append(f"{dataset_id}.{key}: {value}")
        if missing:
            raise FileNotFoundError(
                f"{manifest_path}: artifact(s) not found:\n  "
                + "\n  ".join(missing)
            )
    return loaded


def write_curated_manifest(
    path: str | Path, manifest: CuratedExampleManifest
) -> Path:
    """Write a manifest, YAML or JSON by suffix.

    Mirrors the ``dataset_manifest`` / ``run_manifest`` writers: dump through
    ``mode="json"`` so paths and enums become plain strings, and do not sort
    keys — the declared order is the author's.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = manifest.model_dump(mode="json", exclude_none=True)
    if out.suffix.lower() in (".yaml", ".yml"):
        out.write_text(
            yaml.safe_dump(payload, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
    else:
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out


__all__ = [
    "SCHEMA_VERSION",
    "CuratedExample",
    "CuratedExampleManifest",
    "ExampleDataset",
    "LoadedExampleManifest",
    "ReviewStatus",
    "SCLEAssessmentEntry",
    "SummaryMode",
    "load_curated_manifest",
    "write_curated_manifest",
]
