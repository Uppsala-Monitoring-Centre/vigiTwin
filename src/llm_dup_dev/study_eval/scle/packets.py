"""Static reviewer packets, built on the existing ``review_html`` renderer.

A packet is one self-contained HTML page per SCLE case — the same side-by-side
report comparison, decision summary, and (optional) frequency-statistics view
the review-HTML tool already produces, plus the reference label surfaced as an
annotation. The structured auto-context (confusion cell, joint stratum) lives in
the reviewer *workbook*, not the packet, so no HTML template change is needed.

Confidential report content never enters the generic input schemas. Instead the
secure adapter implements :class:`PacketContentProvider` to hand this module the
``Report`` objects (and any frozen features/classification/bundle) for a given
pair at packet-generation time, inside the secure environment. Dev-side tests
implement the same protocol over synthetic reports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from llm_dup_dev.data.offline import FrozenPairBundle
from llm_dup_dev.models.classification import ClassificationResult
from llm_dup_dev.models.pair_features import PairFeatures
from llm_dup_dev.models.report import Report
from llm_dup_dev.review_html import PairRenderInput, render_batch_html
from llm_dup_dev.review_html.render import _safe_filename
from llm_dup_dev.study_eval.sampling import SCLESample


class PacketContent(BaseModel):
    """The per-pair content needed to render one reviewer packet.

    Only the two reports are required. ``features`` / ``classification`` /
    ``frozen_bundle`` enrich the packet with the evidence, decision, and
    frequency-statistics sections when available. ``external_url`` renders a
    header link (e.g. to an internal case viewer).
    """

    model_config = {"arbitrary_types_allowed": True}

    report_1: Report
    report_2: Report
    features: PairFeatures | None = None
    classification: ClassificationResult | None = None
    frozen_bundle: FrozenPairBundle | None = None
    external_url: str | None = None


class PacketContentProvider(Protocol):
    """Supplies packet content for a pair id.

    The secure adapter implements this over confidential storage; dev-side
    tests implement it over synthetic reports. Mirrors the ``ReportLoader`` /
    ``StatsProvider`` seam in :mod:`llm_dup_dev.data.protocols`.
    """

    def get(self, pair_id: str) -> PacketContent: ...


def packet_filename(pair_id: str) -> str:
    """The packet HTML filename for ``pair_id`` (matches ``review_html``)."""
    return f"{_safe_filename(pair_id)}.html"


def packet_targets(sample: SCLESample, packet_dir: str = "packets") -> dict[str, str]:
    """Relative hyperlink target per pair, for linking a workbook to its packets.

    Returns ``{pair_id: "{packet_dir}/{packet_filename(pair_id)}"}`` for every
    case in ``sample`` (forward slashes; relative to the workbook file). The
    target is deterministic from ``pair_id`` and matches what
    :func:`generate_packets` / ``review_html`` writes, so it resolves once the
    packets are generated into ``packet_dir`` beside the workbook.
    """
    prefix = f"{packet_dir}/" if packet_dir else ""
    return {case.pair_id: f"{prefix}{packet_filename(case.pair_id)}" for case in sample.cases}


def generate_packets(
    sample: SCLESample,
    provider: PacketContentProvider,
    output_dir: str | Path,
    *,
    write_index: bool = True,
) -> dict[str, str]:
    """Render one HTML packet per case in ``sample`` into ``output_dir``.

    Returns a mapping from ``pair_id`` to the written packet filename, so the
    workbook generator can link each case row to its packet. The reference
    label is passed through as an annotation so it appears on the page; the
    ``review_html`` renderer preserves the study's reference vocabulary
    (``duplicate`` / ``non_duplicate``) verbatim when it is outside the
    pipeline's own label set.
    """
    out_dir = Path(output_dir)
    items: list[PairRenderInput] = []
    filenames: dict[str, str] = {}
    for case in sample.cases:
        content = provider.get(case.pair_id)
        items.append(
            PairRenderInput(
                report_1=content.report_1,
                report_2=content.report_2,
                pair_id=case.pair_id,
                features=content.features,
                classification=content.classification,
                frozen_bundle=content.frozen_bundle,
                external_url=content.external_url,
                annotation={
                    "duplicates_assessment": case.auto_context.reference_label
                },
            )
        )
        filenames[case.pair_id] = packet_filename(case.pair_id)

    render_batch_html(items, out_dir, write_index=write_index)
    return filenames


__all__ = [
    "packet_targets",
    "PacketContent",
    "PacketContentProvider",
    "packet_filename",
    "generate_packets",
]
