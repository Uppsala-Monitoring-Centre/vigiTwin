"""Turn a curated manifest into evidence cases, reading each run once.

Examples are grouped by dataset so each saved run is loaded a single time,
with a selection naming only the pairs that collection wants. A two-pair
collection drawn from a thirty-pair reference set therefore costs one read of
that set's artifacts and two view models — not thirty.

Manifest order is preserved across the whole collection, including when its
examples interleave datasets: the order a curator chose is part of the
editorial content.
"""

from __future__ import annotations

from pathlib import Path

from ..formats import evidence_case_from_result, reference_from_annotation
from ..selection import MissingPairPolicy, PairSelection
from .build import ExamplePresentation, ReferenceInfo, SCLEAssessment
from .screening import screening_verdict
from .manifest import CuratedExample, LoadedExampleManifest, SummaryMode
from .models import EvidenceCaseVM, EvidencePageVM
from .settings import EvidenceRunSettings, load_evidence_run_settings


def _presentation(example: CuratedExample, index: int) -> ExamplePresentation:
    """The curator's framing for one example.

    The example id doubles as the filename stem and the section anchor, so a
    missing one falls back to a positional ``example-N`` rather than to the
    pair id — a supplement refers to its examples by position, and a pair id
    is an internal identifier a reader has no use for.
    """
    mode = example.resolved_summary_mode()
    return ExamplePresentation(
        example_id=example.example_id or f"example-{index + 1}",
        kicker=example.kicker or "",
        title=example.title or "",
        lede=example.lede or "",
        case_interpretation=example.case_interpretation,
        summary_mode=(
            "both" if mode is SummaryMode.BOTH else mode.value
        ),
    )


def _reference(
    example: CuratedExample,
    annotation: dict | None,
    *,
    set_label: str | None,
    screening_algorithm: str | None,
    screening_retrieved: bool | None,
    screening_note: str,
    positive_class: str,
) -> ReferenceInfo:
    """Reference metadata, taken from the artifacts and the SCLE entry only.

    The reference label comes from the run's annotations, never from the
    manifest, so a curated collection cannot assert a verdict the data does
    not carry. The SCLE assessment is the one judgement the manifest supplies,
    because no artifact holds it.
    """
    base = reference_from_annotation(
        annotation,
        set_label=set_label,
        screening_retrieved=screening_retrieved,
        screening_note=screening_note,
        screening_algorithm=screening_algorithm or "vigiMatch",
        positive_class=positive_class,
    )
    entry = example.scle_assessment
    scle = SCLEAssessment(
        state=entry.state,
        issue=entry.issue.value if entry and entry.issue else None,
        note=entry.note or "",
    ) if entry is not None else SCLEAssessment()
    return ReferenceInfo(
        reference_label=base.reference_label,
        set_label=base.set_label,
        screening_retrieved=base.screening_retrieved,
        screening_note=base.screening_note,
        screening_algorithm=base.screening_algorithm,
        scle=scle,
        positive_class=base.positive_class,
    )


def build_collection(
    loaded: LoadedExampleManifest,
    *,
    on_missing: MissingPairPolicy = MissingPairPolicy.ERROR,
    strict: bool = True,
) -> EvidencePageVM:
    """Build every case a manifest names, in manifest order."""
    # Imported here rather than at module level: ``saved_run`` reaches back
    # into ``review_html`` and into this package's settings module, so a
    # module-level import closes a cycle.
    from ..saved_run import load_saved_run

    manifest = loaded.manifest
    cases_by_key: dict[tuple[str, str], EvidenceCaseVM] = {}

    by_dataset: dict[str, list[tuple[int, CuratedExample]]] = {}
    for index, example in enumerate(manifest.examples):
        by_dataset.setdefault(example.dataset, []).append((index, example))

    for dataset_id, entries in by_dataset.items():
        run_settings: EvidenceRunSettings | None = None
        run_manifest = loaded.run_manifest_path(dataset_id)
        if run_manifest is not None:
            run_settings = load_evidence_run_settings(run_manifest)

        selection = PairSelection(
            pair_ids=tuple(example.pair_id for _, example in entries),
            on_missing=on_missing,
        )
        kwargs = loaded.dataset_paths(dataset_id)
        items, _summary = load_saved_run(
            select=selection,
            strict=strict,
            include_debug=False,
            **kwargs,  # type: ignore[arg-type]
        )
        loaded_by_pair = {item.result.pair_id: item for item in items}

        dataset = manifest.datasets[dataset_id]
        set_label = dataset.label or dataset_id
        verdict_key = loaded.screening_verdict_key(dataset_id)
        for index, example in entries:
            item = loaded_by_pair.get(example.pair_id)
            if item is None:
                # Only reachable under MissingPairPolicy.SKIP; the default
                # policy already raised inside load_saved_run.
                continue
            # Read per pair from its own screening entry. Before this, the
            # collection path passed no verdict at all, so a curated review
            # said "Not evaluated" even when the manifest named a screening
            # artifact that covered the pair.
            verdict = screening_verdict(item.screening, key=verdict_key)
            cases_by_key[(dataset_id, example.pair_id)] = (
                evidence_case_from_result(
                    item,
                    presentation=_presentation(example, index),
                    reference=_reference(
                        example,
                        item.annotation,
                        set_label=set_label,
                        screening_algorithm=item.screening_algorithm,
                        screening_retrieved=verdict.retrieved,
                        screening_note=verdict.note,
                        positive_class=manifest.positive_class,
                    ),
                    run_settings=run_settings,
                )
            )

    ordered = [
        cases_by_key[(e.dataset, e.pair_id)]
        for e in manifest.examples
        if (e.dataset, e.pair_id) in cases_by_key
    ]
    return EvidencePageVM(
        title=manifest.title or "Evidence review",
        intro=manifest.intro or "",
        note=manifest.note or "",
        cases=ordered,
    )


def export_summary_drafts(page: EvidencePageVM) -> dict[str, str]:
    """The generated summary for each case, as a starting point for editing.

    Returned rather than written into the manifest. A curator pastes a draft
    in, edits it, and from then on it is their text — so the generator must
    have no way to write back over it, which is guaranteed here by its not
    having one.
    """
    drafts: dict[str, str] = {}
    for case in page.cases:
        generated = next(
            (b for b in case.narrative_blocks if not b.is_human), None
        )
        if generated is not None:
            drafts[case.example_id] = "\n\n".join(generated.paragraphs)
    return drafts


def write_summary_drafts(
    page: EvidencePageVM, path: str | Path, *, force: bool = False
) -> Path:
    """Write the generated drafts to a separate file.

    Refuses to overwrite an existing file without ``force``: by the second run
    that file usually holds edited text, and silently replacing a curator's
    work with a regenerated draft is the one failure this whole arrangement
    exists to prevent.
    """
    import json

    out = Path(path)
    if out.exists() and not force:
        raise FileExistsError(
            f"{out} already exists. It may hold edited text; pass force=True "
            f"(CLI: --force) to replace it."
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(export_summary_drafts(page), indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    return out


__all__ = ["build_collection", "export_summary_drafts", "write_summary_drafts"]
