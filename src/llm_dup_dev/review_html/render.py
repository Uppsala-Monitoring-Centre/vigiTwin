"""HTML rendering: view models -> standalone HTML files.

Uses Jinja2 with templates in the ``templates/`` subdirectory.
All CSS is embedded inline so the output is fully self-contained.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import jinja2

from typing import TYPE_CHECKING, Sequence

from .models import BatchIndexItem, ComparisonStatus, PairViewModel

if TYPE_CHECKING:  # pragma: no cover
    # Imported lazily in the evidence functions below. At module level this
    # would be a cycle: the evidence package reaches back to this module and
    # to ``saved_run``, which in turn imports from ``review_html`` itself.
    from .evidence.models import (
        EvidenceCaseVM,
        EvidenceIndexItem,
        EvidenceIndexVM,
        EvidencePageVM,
        ProvenanceVM,
    )

# ---------------------------------------------------------------------------
# Jinja2 environment
# ---------------------------------------------------------------------------

_TEMPLATE_DIR = Path(__file__).parent / "templates"

_STATUS_ICONS: dict[ComparisonStatus, str] = {
    ComparisonStatus.MATCH: "match",
    ComparisonStatus.PARTIAL_MATCH: "partial",
    ComparisonStatus.MISMATCH: "diff",
    ComparisonStatus.MISSING_LEFT: "R2 only",
    ComparisonStatus.MISSING_RIGHT: "R1 only",
    ComparisonStatus.INFORMATIONAL: "info",
    ComparisonStatus.NEUTRAL: "",
}


def _status_icon(status: ComparisonStatus) -> str:
    """Map a ComparisonStatus to a unicode icon for the template."""
    return _STATUS_ICONS.get(status, "?")


def _get_jinja_env() -> jinja2.Environment:
    """Create a Jinja2 environment loading from the templates directory."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=jinja2.select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals["status_icon"] = _status_icon
    return env


# ---------------------------------------------------------------------------
# Rendering functions
# ---------------------------------------------------------------------------


def render_pair(
    vm: PairViewModel,
    summary: BatchIndexItem | None = None,
) -> str:
    """Render a single pair comparison to a standalone HTML string.

    The top-of-page summary panel is built from the same
    :class:`BatchIndexItem` that drives the batch-index row, so the two
    presentations stay identical. When *summary* is not supplied it is
    built here (the ``filename`` arg is only used for the index
    hyperlink, so an empty string is fine).
    """
    from .compare import build_batch_index_item

    if summary is None:
        summary = build_batch_index_item(vm, "")
    env = _get_jinja_env()
    template = env.get_template("pair.html.j2")
    return template.render(vm=vm, summary=summary)


def render_index(items: list[BatchIndexItem]) -> str:
    """Render the batch index page."""
    env = _get_jinja_env()
    template = env.get_template("index.html.j2")
    return template.render(
        items=items,
        generated_at=datetime.now(tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        ),
        has_labels=any(item.final_label is not None for item in items),
        has_l_match=any(item.l_match is not None for item in items),
        has_inconsistencies=any(
            item.inconsistency_items for item in items
        ),
        has_penalty=any(item.penalty_text is not None for item in items),
        has_anchor=any(item.anchor_label is not None for item in items),
        has_anchor_n=any(item.anchor_n is not None for item in items),
        has_context=any(
            item.context_label is not None or item.source_case is not None
            for item in items
        ),
        has_explicit_link=any(
            item.explicit_link is not None for item in items
        ),
        has_matching_features=any(
            item.matching_features is not None for item in items
        ),
        has_annotations=any(
            item.annotation_label is not None for item in items
        ),
    )


# ---------------------------------------------------------------------------
# File-writing helpers
# ---------------------------------------------------------------------------

DEFAULT_OUTPUT_DIR = Path("outputs/review_html")


def _safe_filename(pair_id: str) -> str:
    """Sanitise a pair_id for use as a filename (replace non-alnum with _)."""
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in pair_id)


def write_pair_html(
    vm: PairViewModel,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> Path:
    """Render and write a pair HTML file.

    Parameters
    ----------
    vm : PairViewModel
        The view model to render.
    output_dir : Path
        Directory to write into (created if absent).

    Returns
    -------
    Path
        Path to the written HTML file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{_safe_filename(vm.pair_id)}.html"
    path = output_dir / filename
    path.write_text(render_pair(vm), encoding="utf-8")
    return path


def write_batch_html(
    view_models: list[PairViewModel],
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    write_index: bool = True,
) -> Path:
    """Render and write HTML files for a batch of pairs.

    Parameters
    ----------
    view_models : list[PairViewModel]
        All pairs to render.
    output_dir : Path
        Directory to write into (created if absent).
    write_index : bool
        Whether to generate an ``index.html`` linking to all pairs.

    Returns
    -------
    Path
        The output directory.
    """
    from .compare import build_batch_index_item

    output_dir.mkdir(parents=True, exist_ok=True)

    index_items: list[BatchIndexItem] = []
    for vm in view_models:
        filename = f"{_safe_filename(vm.pair_id)}.html"
        path = output_dir / filename
        # Build the index item once and reuse it for both the pair-page
        # summary panel and the index row so the two stay identical.
        index_item = build_batch_index_item(vm, filename)
        path.write_text(render_pair(vm, index_item), encoding="utf-8")
        index_items.append(index_item)

    if write_index and index_items:
        index_path = output_dir / "index.html"
        index_path.write_text(render_index(index_items), encoding="utf-8")

    return output_dir


# ---------------------------------------------------------------------------
# Evidence format
#
# Added alongside the detailed format rather than threaded into it. The
# functions above keep their signatures and their output byte for byte, so
# ``study_eval.scle.packets`` — which reimplements the detailed format's
# filename rule and builds on ``write_batch_html`` — is unaffected. The
# evidence format writes into its own output directory, which is also what a
# curated collection wants.
# ---------------------------------------------------------------------------

DEFAULT_EVIDENCE_OUTPUT_DIR = Path("outputs/evidence_review")


def render_evidence_page(vm: "EvidencePageVM") -> str:
    """Render one evidence document, holding one or more cases."""
    env = _get_jinja_env()
    return env.get_template("evidence_page.html.j2").render(vm=vm)


def render_evidence_index(vm: "EvidenceIndexVM") -> str:
    """Render the index over a set of generated evidence reviews."""
    env = _get_jinja_env()
    return env.get_template("evidence_index.html.j2").render(vm=vm)


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def evidence_filename(case: "EvidenceCaseVM") -> str:
    """The HTML filename for one case, from its example id.

    Uses the example id rather than the pair id so a curated collection's
    files are named S1, S2, … as the collection refers to them. The id falls
    back to the pair id when no example id was supplied.
    """
    return f"{_safe_filename(case.example_id)}.html"


def write_evidence_html(
    case: "EvidenceCaseVM",
    output_dir: Path = DEFAULT_EVIDENCE_OUTPUT_DIR,
    *,
    title: str | None = None,
    intro: str = "",
    note: str = "",
    provenance: "ProvenanceVM | None" = None,
) -> Path:
    """Render and write one case as its own evidence document.

    ``provenance`` names the run behind the cases, for the footer. Omitted, the
    footer still states the rendering version, which needs no run manifest.
    """
    from .evidence.models import EvidencePageVM

    output_dir.mkdir(parents=True, exist_ok=True)
    page = EvidencePageVM(
        title=title or (case.title or f"Report pair {case.pair_id}"),
        intro=intro,
        note=note,
        generated_at=_utc_now(),
        **({} if provenance is None else {"provenance": provenance}),
        cases=[case],
    )
    path = output_dir / evidence_filename(case)
    path.write_text(render_evidence_page(page), encoding="utf-8")
    return path


def write_evidence_batch_html(
    cases: Sequence["EvidenceCaseVM"],
    output_dir: Path = DEFAULT_EVIDENCE_OUTPUT_DIR,
    *,
    write_index: bool = True,
    index_title: str = "Evidence review",
    collection_intro: str = "",
    collection_note: str = "",
    provenance: "ProvenanceVM | None" = None,
) -> Path:
    """Write one evidence document per case, plus an optional index.

    Case order is preserved: a curated collection's order is a deliberate
    choice by whoever assembled it. The index links only the files written.
    """
    from .evidence.models import (
        EvidenceIndexItem,
        EvidenceIndexVM,
        number_cases,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    # Each case is written into its own file, so its place in the set has to
    # travel with it; a page that holds one case cannot work it out from the
    # page. Cases that arrive already numbered — a curated collection's —
    # keep their numbers.
    cases = number_cases(cases)
    items: list[EvidenceIndexItem] = []
    for case in cases:
        write_evidence_html(
            case,
            output_dir,
            intro=collection_intro,
            note=collection_note,
            provenance=provenance,
        )
        label = next(
            (
                tile.value
                for tile in case.outcomes
                if tile.label == "vigiTwin classification"
            ),
            None,
        )
        items.append(
            EvidenceIndexItem(
                example_id=case.example_id,
                example_label=case.example_label,
                pair_id=case.pair_id,
                filename=evidence_filename(case),
                title=case.title,
                kicker=case.kicker,
                final_label=label,
                reference_label=(
                    case.reference_verdict.display
                    if case.reference_verdict is not None
                    else None
                ),
            )
        )

    if write_index and items:
        index = EvidenceIndexVM(
            title=index_title, generated_at=_utc_now(), items=items
        )
        (output_dir / "index.html").write_text(
            render_evidence_index(index), encoding="utf-8"
        )
    return output_dir


def write_evidence_document(
    cases: Sequence["EvidenceCaseVM"],
    path: Path,
    *,
    title: str,
    intro: str = "",
    note: str = "",
    provenance: "ProvenanceVM | None" = None,
) -> Path:
    """Write every case into one document.

    Present now because the template already supports it and it costs nothing;
    a later phase builds the supplementary document on top of this, with the
    publication transforms applied to the cases before they arrive here.
    """
    from .evidence.models import EvidencePageVM, number_cases

    path.parent.mkdir(parents=True, exist_ok=True)
    page = EvidencePageVM(
        title=title,
        intro=intro,
        note=note,
        generated_at=_utc_now(),
        **({} if provenance is None else {"provenance": provenance}),
        cases=number_cases(cases),
    )
    path.write_text(render_evidence_page(page), encoding="utf-8")
    return path
