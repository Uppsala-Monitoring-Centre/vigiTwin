"""The two review formats, and the one place the choice between them is made.

``detailed`` is the existing pair review: dense, diagnostic, built for
annotation and debugging. ``evidence`` is the reader-facing review of what
vigiTwin concluded and on what basis.

A format is a (builder, view model, template, writer) triple rather than a
flag threaded through the existing code path. That keeps the detailed
format's functions — ``render_pair``, ``write_pair_html``,
``write_batch_html``, ``_safe_filename`` — byte-for-byte unchanged, which
matters because ``study_eval.scle.packets`` reimplements the detailed
format's filename rule and builds reviewer packets on top of it. The two
formats also write into separate directories, so neither can overwrite the
other's index.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Mapping, Sequence

from .evidence.build import (
    BUILTIN_REFERENCE_TERMS,
    ExamplePresentation,
    ReferenceInfo,
    build_evidence_case,
    normalise_reference_term,
)
from .evidence.models import EvidenceCaseVM
from .evidence.screening import screening_verdict
from .evidence.settings import EvidenceRunSettings

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime only
    from . import ResultRenderInput


class ReviewFormat(str, Enum):
    """Which review to generate."""

    DETAILED = "detailed"
    EVIDENCE = "evidence"


def reference_from_annotation(
    annotation: dict | None,
    *,
    set_label: str | None = None,
    screening_retrieved: bool | None = None,
    screening_algorithm: str = "vigiMatch",
    screening_note: str = "",
    positive_class: str = "primary",
    label_map: Mapping[str, str] | None = None,
) -> ReferenceInfo:
    """Read a reference label out of a saved run's annotation entry.

    Only the study's binary vocabulary is accepted. An annotation written in
    the pipeline's vocabulary is left unread rather than reinterpreted —
    showing it as a reference label would present one method's output as a
    human verdict.

    ``label_map`` carries the vocabulary of a *particular* reference set,
    supplied by whoever knows how that set was read in the analysis. A set
    annotating ``Otherwise related`` and collapsing it to non-duplicates for
    the study says so there; this package cannot know that collapse, and
    guessing it would be inventing a human verdict. Built-in terms win over a
    supplied one, so a collection cannot redefine ``duplicate``.

    A value that maps to nothing is reported on ``reference_note`` rather than
    dropped. An unreadable label used to be indistinguishable from an absent
    one — both showed "Not supplied" — which is how a whole reference set can
    go unread without anyone noticing.
    """
    label: str | None = None
    note = ""
    if annotation:
        raw = str(annotation.get("duplicates_assessment", "")).strip()
        term = normalise_reference_term(raw)
        if term:
            label = BUILTIN_REFERENCE_TERMS.get(term)
            if label is None and label_map:
                label = label_map.get(term)
            if label is None:
                note = (
                    f"The reference annotation reads {raw!r}, which is not a "
                    "term this review knows. It is shown as not supplied "
                    "rather than guessed at; give the set's own vocabulary "
                    "under 'reference_label_map' in the collection manifest."
                )
    return ReferenceInfo(
        reference_label=label,
        set_label=set_label,
        screening_retrieved=screening_retrieved,
        screening_algorithm=screening_algorithm,
        screening_note=screening_note,
        reference_note=note,
        positive_class=positive_class,
    )


def evidence_case_from_result(
    item: "ResultRenderInput",
    *,
    presentation: ExamplePresentation | None = None,
    reference: ReferenceInfo | None = None,
    run_settings: EvidenceRunSettings | None = None,
    set_label: str | None = None,
    screening_verdict_key: str | None = None,
) -> EvidenceCaseVM:
    """Build an evidence case from one loaded saved-run item.

    When no ``reference`` is supplied, one is assembled from the item: the
    reference label from its annotation and the screening outcome from its
    screening entry, read with :func:`.screening.screening_verdict`.
    """
    result = item.result
    if reference is None:
        verdict = screening_verdict(item.screening, key=screening_verdict_key)
        reference = reference_from_annotation(
            item.annotation,
            set_label=set_label,
            screening_retrieved=verdict.retrieved,
            screening_note=verdict.note,
            screening_algorithm=item.screening_algorithm or "vigiMatch",
        )
    return build_evidence_case(
        report_1=item.report_1,
        report_2=item.report_2,
        pair_id=result.pair_id,
        features=result.features,
        classification=result.classification,
        frozen_bundle=item.frozen_bundle,
        narrative_demographics_1=result.narrative_demographics_1,
        narrative_demographics_2=result.narrative_demographics_2,
        reference=reference,
        presentation=presentation,
        run_settings=run_settings,
    )


def render_items(
    items: Sequence["ResultRenderInput"],
    output_dir: Path,
    *,
    review_format: ReviewFormat = ReviewFormat.DETAILED,
    write_index: bool = True,
    run_settings: EvidenceRunSettings | None = None,
    set_label: str | None = None,
) -> Path:
    """Render loaded saved-run items in the requested format."""
    from . import render_results_html
    from .render import write_evidence_batch_html

    if review_format is ReviewFormat.DETAILED:
        return render_results_html(
            items, output_dir=output_dir, write_index=write_index
        )

    cases = [
        evidence_case_from_result(
            item, run_settings=run_settings, set_label=set_label
        )
        for item in items
    ]
    return write_evidence_batch_html(
        cases, output_dir, write_index=write_index
    )


__all__ = [
    "ReviewFormat",
    "evidence_case_from_result",
    "reference_from_annotation",
    "render_items",
]
