"""HTML rendering: view models -> standalone HTML files.

Uses Jinja2 with templates in the ``templates/`` subdirectory.
All CSS is embedded inline so the output is fully self-contained.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import jinja2

from .models import BatchIndexItem, ComparisonStatus, PairViewModel

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


def render_pair(vm: PairViewModel) -> str:
    """Render a single pair comparison to a standalone HTML string."""
    env = _get_jinja_env()
    template = env.get_template("pair.html.j2")
    return template.render(vm=vm)


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
        path.write_text(render_pair(vm), encoding="utf-8")
        index_items.append(build_batch_index_item(vm, filename))

    if write_index and index_items:
        index_path = output_dir / "index.html"
        index_path.write_text(render_index(index_items), encoding="utf-8")

    return output_dir
