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
import re
from collections import Counter
from collections.abc import Mapping
from enum import Enum
from pathlib import Path

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from llm_dup_dev.study_eval.scle.form import ReferenceLabelIssue

from ..render import _safe_filename
from .build import (
    BUILTIN_REFERENCE_TERMS,
    REFERENCE_VOCABULARY,
    SCLEState,
    normalise_reference_term,
)

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


def _check_reference_label_map(mapping: Mapping[str, str], where: str) -> None:
    """Reject a reference-label mapping that cannot mean what it says.

    Three ways it can be wrong, each silent if unchecked: a target outside
    the study's binary vocabulary (so the page would show a label no estimand
    knows), a key that redefines a built-in term (so a manifest could quietly
    make ``duplicate`` mean non-duplicate), and two keys that fold to the
    same term (so which one applied would depend on dict order).
    """
    if not mapping:
        return
    bad = sorted(
        f"{key!r} -> {value!r}"
        for key, value in mapping.items()
        if value not in REFERENCE_VOCABULARY
    )
    if bad:
        raise ValueError(
            f"{where} may only map to the study's reference vocabulary "
            f"({', '.join(sorted(REFERENCE_VOCABULARY))}); got "
            f"{'; '.join(bad)}."
        )
    seen: dict[str, str] = {}
    for key in mapping:
        term = normalise_reference_term(key)
        if not term:
            raise ValueError(
                f"{where} has an empty key; a term with no letters cannot be "
                "matched against an annotation."
            )
        if term in BUILTIN_REFERENCE_TERMS:
            raise ValueError(
                f"{where} may not redefine {key!r}: {term!r} is part of the "
                "study's own vocabulary."
            )
        if term in seen:
            raise ValueError(
                f"{where} keys {seen[term]!r} and {key!r} are the same term "
                "once case and punctuation are folded."
            )
        seen[term] = key


def _fold_reference_label_map(mapping: Mapping[str, str]) -> dict[str, str]:
    """A mapping keyed by folded term, ready to look an annotation up."""
    return {
        normalise_reference_term(key): value for key, value in mapping.items()
    }


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
    #: Whether this run used explicit inter-report case links, for runs whose
    #: manifest predates the setting. ``False`` tells the review not to
    #: describe the case-identifier fields as checked: a run configured
    #: without the feature still writes an ``explicit_link`` field, and the
    #: page read that as "neither report carried case-identifier data" —
    #: blaming the data for a check nobody ran.
    #:
    #: Only ever fills what the run manifest did not record. Where both say
    #: something and they disagree, the collection fails to build rather than
    #: picking one: the manifest is the run's own account of itself, and a
    #: declaration that contradicts it is an error in the declaration.
    explicit_link_enabled: bool | None = None
    #: This set's annotation vocabulary, mapped to the study's binary one —
    #: ``{"Otherwise related": "non_duplicate"}``. Per dataset for the same
    #: reason as ``screening_verdict_key``: a reference label is read out of
    #: *this* dataset's ``annotations`` file, and a collection spanning two
    #: reference sets may be spanning two vocabularies.
    #:
    #: Where set, it **replaces** the collection-level default rather than
    #: adding to it, so what a dataset declares is what it uses. Merging
    #: would let a set inherit a term it does not use and, worse, a term its
    #: annotators meant differently — and a wrongly mapped label is shown as
    #: a human verdict, where an unmapped one is reported as a gap. Between a
    #: silent wrong answer and a loud missing one, this takes the loud one.
    reference_label_map: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_artifacts(self) -> "ExampleDataset":
        _check_reference_label_map(
            self.reference_label_map, "a dataset's reference_label_map"
        )
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


#: An example id has three jobs at once — the filename stem, the HTML section
#: anchor, and the label the page shows and surrounding text cites — so it has
#: to be valid in all three. A leading letter keeps it usable in a CSS
#: selector; the rest is what survives a URL fragment and a filename
#: unchanged, with ``.`` allowed because an id like ``S1.a`` is a reasonable
#: thing for a curator to want.
_EXAMPLE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")


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

    @field_validator("example_id")
    @classmethod
    def _id_is_usable_as_an_anchor(cls, value: str | None) -> str | None:
        """Reject an id that cannot be what it is used as.

        ``example_id`` becomes an HTML ``id`` attribute and a URL fragment,
        so a space or a slash in it yields a section no reference can reach —
        silently, in a document whose whole purpose is being cited. Caught
        here rather than at render time, where the output looks fine until
        someone clicks the link.
        """
        if value is None:
            return None
        if not _EXAMPLE_ID_RE.match(value):
            raise ValueError(
                f"example_id {value!r} cannot be used as a section anchor: it "
                f"must start with a letter and hold only letters, digits, "
                f"'.', '-' or '_'."
            )
        return value

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
    #: The annotation vocabulary to assume for datasets that declare none,
    #: mapped to the study's binary one: ``{"Otherwise related":
    #: "non_duplicate"}``. A reference set may annotate in terms no other set
    #: uses, and how those terms were read in the analysis is a study
    #: decision that lives with the study — this package cannot infer it, and
    #: guessing would be inventing a human verdict. Keys are matched
    #: case-insensitively, with spaces, underscores and hyphens treated
    #: alike.
    #:
    #: A single-set collection says it once here. A collection spanning two
    #: reference sets puts each set's own vocabulary on its dataset, which
    #: replaces this.
    reference_label_map: dict[str, str] = Field(default_factory=dict)
    datasets: dict[str, ExampleDataset]
    examples: list[CuratedExample]

    @model_validator(mode="after")
    def _check_collection(self) -> "CuratedExampleManifest":
        if not self.datasets:
            raise ValueError("a manifest needs at least one dataset.")
        if not self.examples:
            raise ValueError("a manifest needs at least one example.")

        _check_reference_label_map(
            self.reference_label_map, "reference_label_map"
        )

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


    def reference_label_map_for(self, dataset: str) -> dict[str, str]:
        """The vocabulary this dataset reads its annotations with, folded.

        The dataset's own mapping where it has one, the collection default
        otherwise. Not the two merged: see
        :attr:`ExampleDataset.reference_label_map` for why a set that
        declares a vocabulary gets exactly the one it declared.
        """
        own = self.datasets[dataset].reference_label_map
        return _fold_reference_label_map(own or self.reference_label_map)


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
