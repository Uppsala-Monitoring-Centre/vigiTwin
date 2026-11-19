"""Writers. The publication one refuses to run while anything is unresolved.

Three outputs, deliberately written by three separate calls to three separate
destinations, because they have three different sensitivities:

* :func:`write_publication_evidence` — the supplement and its public
  manifest. **Fail-closed**: raises :class:`PublicationBlocked` while the
  disclosure report holds a single unresolved finding. There is no override
  argument. Clearing a finding means supplying approved replacement text or a
  :class:`~.policy.FindingResolution` naming a reviewer, which is a decision
  with a signature on it rather than a flag.
* :func:`write_scrambling_key` — the secure record of offsets and mappings.
  Caller chooses the path; it must be inside the secure environment.
* :func:`write_side_by_side` — the internal and publication presentations of
  the same collection, plus a disclosure review page listing every value that
  changed and every finding. Also secure: it holds the originals.

Rendering reuses the existing evidence writers unchanged. A publication page
is the same template over transformed cases, which is the property the view
model was designed for; the only thing the writers needed was the mode for
their footer.
"""

from __future__ import annotations

import json
from pathlib import Path

from ...render import (
    _get_jinja_env,
    _safe_filename,
    render_evidence_page,
    write_evidence_batch_html,
    write_evidence_document,
)
from ..models import EvidencePageVM, PresentationMode, number_cases
from .findings import DisclosureReport
from .key import ScramblingKeyRecord, build_key_record
from .manifest import PublicationTransformManifest, build_publication_manifest
from .scramble import CaseTransform, ScrambleResult
from .substitute import SubstitutionTable, scan_rendered_html

#: Filenames inside the publication output directory.
MANIFEST_FILENAME = "publication_manifest.json"
#: Filename of the secure disclosure review page.
REVIEW_FILENAME = "disclosure_review.html"


class PublicationBlocked(RuntimeError):
    """The candidate holds unresolved disclosure findings and was not written.

    Carries the report so a caller can print it, and nothing else: there is
    no ``force`` and no partial write. A half-written supplement next to an
    exception is the failure mode this class exists to prevent.
    """

    def __init__(self, report: DisclosureReport) -> None:
        super().__init__(
            f"publication export blocked — {report.summary()}. Supply "
            "approved replacement text or a finding resolution for each, or "
            "handle the example manually."
        )
        self.report = report


def _union_table(result: ScrambleResult) -> SubstitutionTable:
    """Every case's mappings in one table, for the page's own prose.

    Cases disagree by construction — each pair has its own offset — so where
    two map one original differently the merged table keeps the first and
    records a conflict it does not report. What it must not lose is the set of
    *published* values: a value one case produced is not something the
    residual scan may look for in another, even if that other case reported
    it.
    """
    table = SubstitutionTable()
    for transform in result.transforms:
        for entry in transform.table.entries.values():
            table.add(
                entry.original, entry.published, kind=entry.kind, note=entry.note
            )
            table.extra_published.add(entry.published)
    return table


def _describe_offsets(transform: CaseTransform) -> str:
    """The date offsets for the review page, as one phrase.

    Names the two blocks separately only when they moved separately, so a
    reviewer reading a run of coupled cases is not asked to parse a
    distinction that did not arise.
    """
    report, clinical = transform.date_offset_days, transform.clinical_offset_days
    if report is None:
        return "not solved"
    if clinical is None or clinical == report:
        return f"{report:+d} days"
    return f"report {report:+d} days, clinical history {clinical:+d} days"


def verify_rendered_output(result: ScrambleResult) -> DisclosureReport:
    """Render every page and look for originals in the HTML itself.

    The transform works on the view model, never on markup; this reads the
    markup to check that nothing a template composes escaped the model walk.
    Its findings join the report, so a leak here blocks the export like any
    other.
    """
    findings = list(result.report.findings)
    for transform, case in zip(result.transforms, result.page.cases, strict=True):
        page = EvidencePageVM(
            title=result.page.title,
            mode=PresentationMode.PUBLICATION,
            cases=[case],
        )
        # The case's own table, never the merged one: two cases carry
        # different offsets, so one case's original is routinely another's
        # published value, and a merged table would report that as a leak.
        findings.extend(
            scan_rendered_html(
                render_evidence_page(page),
                transform.table,
                example_id=transform.example_id,
            )
        )
    return DisclosureReport(
        findings=findings,
        resolved=list(result.report.resolved),
        advisories=list(result.report.advisories),
    )


def write_publication_evidence(
    result: ScrambleResult,
    output_dir: Path,
    *,
    index_title: str = "Evidence review",
    collection_intro: str = "",
    collection_note: str = "",
    single_document: Path | None = None,
    verify_html: bool = True,
) -> Path:
    """Write the supplement and its public manifest, or refuse to.

    Raises :class:`PublicationBlocked` before writing anything if the
    disclosure report is not clear. Nothing is created in *output_dir* in that
    case — a blocked run leaves no artifact to be mistaken for a cleared one.
    """
    report = verify_rendered_output(result) if verify_html else result.report
    if report.blocked:
        raise PublicationBlocked(report)

    output_dir.mkdir(parents=True, exist_ok=True)
    if single_document is not None:
        write_evidence_document(
            result.page.cases,
            single_document,
            title=result.page.title,
            intro=collection_intro or result.page.intro,
            note=collection_note or result.page.note,
            provenance=result.page.provenance,
            mode=PresentationMode.PUBLICATION,
        )
    write_evidence_batch_html(
        result.page.cases,
        output_dir,
        index_title=index_title,
        collection_intro=collection_intro or result.page.intro,
        collection_note=collection_note or result.page.note,
        provenance=result.page.provenance,
        mode=PresentationMode.PUBLICATION,
    )
    manifest = build_publication_manifest(result)
    (output_dir / MANIFEST_FILENAME).write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    return output_dir


def write_scrambling_key(result: ScrambleResult, path: Path) -> Path:
    """Write the secure key record.

    A separate call to a separate path on purpose: the one way to be sure the
    mappings never land in the publication directory is for the code that
    writes the supplement never to write them.
    """
    record = build_key_record(result)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def write_side_by_side(
    internal: EvidencePageVM,
    result: ScrambleResult,
    output_dir: Path,
    *,
    title: str = "Disclosure review",
) -> Path:
    """Both presentations and the review page that compares them.

    Secure-side output. ``internal/`` and ``publication/`` hold the two
    renderings of the same cases, and :data:`REVIEW_FILENAME` lists, per case,
    every value that changed, every field a human has to decide, and every
    finding — which is the document a disclosure reviewer actually works
    from.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    write_evidence_batch_html(
        number_cases(internal.cases),
        output_dir / "internal",
        index_title=f"{title} — internal",
        provenance=internal.provenance,
        mode=PresentationMode.INTERNAL,
    )
    write_evidence_batch_html(
        result.page.cases,
        output_dir / "publication",
        index_title=f"{title} — publication candidate",
        provenance=result.page.provenance,
        mode=PresentationMode.PUBLICATION,
    )

    rows = []
    for transform, published in zip(
        result.transforms, result.page.cases, strict=True
    ):
        rows.append(
            {
                "example_id": transform.example_id,
                "pair_id": transform.pair_id,
                "published_example_id": transform.published_example_id,
                "published_label": transform.published_example_label,
                "internal_href": (
                    f"internal/{_safe_filename(transform.example_id)}.html"
                ),
                "publication_href": (
                    f"publication/{_safe_filename(published.example_id)}.html"
                ),
                "date_offset": _describe_offsets(transform),
                "numeric_offsets": [
                    f"{name}: {offset.describe()}"
                    for name, offset in sorted(transform.numeric_offsets.items())
                ],
                "dates_total": transform.dates_total,
                "dates_changed": transform.dates_changed,
                "date_pattern_unchanged": transform.date_pattern_unchanged,
                "countries_masked": transform.countries_masked,
                "transformed": [
                    entry
                    for entry in transform.table.entries.values()
                    if entry.changed
                ],
                "unchanged": [
                    entry
                    for entry in transform.table.entries.values()
                    if not entry.changed
                ],
                "separations": transform.recomputed_separations,
                "withheld_paths": transform.withheld_paths,
                "replaced_paths": transform.replaced_paths,
                "approved_as_written_paths": (
                    transform.approved_as_written_paths
                ),
                "findings": [
                    f
                    for f in result.report.findings
                    if f.example_id == transform.example_id
                ],
                "resolved": [
                    f
                    for f in result.report.resolved
                    if f.example_id == transform.example_id
                ],
                "advisories": [
                    f
                    for f in result.report.advisories
                    if f.example_id == transform.example_id
                ],
            }
        )

    template = _get_jinja_env().get_template("disclosure_review.html.j2")
    (output_dir / REVIEW_FILENAME).write_text(
        template.render(
            title=title,
            blocked=result.report.blocked,
            summary=result.report.summary(),
            key_id=result.key_id,
            policy_version=result.policy.policy_version,
            rows=rows,
        ),
        encoding="utf-8",
    )
    return output_dir


__all__ = [
    "MANIFEST_FILENAME",
    "REVIEW_FILENAME",
    "PublicationBlocked",
    "PublicationTransformManifest",
    "ScramblingKeyRecord",
    "verify_rendered_output",
    "write_publication_evidence",
    "write_scrambling_key",
    "write_side_by_side",
]
