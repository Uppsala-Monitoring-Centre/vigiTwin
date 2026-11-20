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
import re
from pathlib import Path

from ...render import (
    EVIDENCE_DOCUMENT_FILENAME,
    _get_jinja_env,
    _safe_filename,
    render_evidence_page,
    write_evidence_batch_html,
    write_evidence_document,
)
from ..models import EvidencePageVM, PresentationMode, number_cases
from .findings import DisclosureFinding, DisclosureReport
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


class NotClearedForRelease(PublicationBlocked):
    """A release was signed, and cannot be honoured as it stands.

    Its own refusal rather than a plain :class:`PublicationBlocked`, because
    the two say different things and a reviewer reading stderr needs the one
    that is true: nothing here is about unresolved findings. Raised rather
    than silently falling back to the candidate wording, which would hand a
    signer a candidate document they had asked to release and might not
    notice.
    """

    def __init__(self, report: DisclosureReport, reason: str) -> None:
        RuntimeError.__init__(
            self, f"release refused — {reason}"
        )
        self.report = report


def _check_release(result: ScrambleResult, report: DisclosureReport) -> None:
    """Whether the signed release may stand.

    Two refusals. A **stale pin** means the document changed after it was
    read, so the signature was given for something else. An **unsigned
    advisory** means the release would assert more than anyone has:
    ``date_pattern_unchanged`` is advisory, so a run can publish a pair's
    real timeline, print one line to stderr and succeed — and a release would
    then call that "cleared for public sharing". An advisory a reviewer has
    actually accepted is no longer an advisory; it moves to
    ``report.resolved`` through a signed ``FindingResolution``. So this asks
    not for the absence of weaknesses but for a signature on each one, which
    is what a release claims on everybody's behalf.
    """
    if result.release is None:
        return
    if result.release_is_stale:
        raise NotClearedForRelease(
            report,
            "the document has changed since it was signed off, so the "
            "release was given for a different one. Re-read it in the "
            "side-by-side review, then sign again with candidate_sha256 set "
            f"to {result.candidate_digest}. Passing --export-approvals "
            "resets the release block to that digest for signing; it leaves "
            "every other signature alone",
        )
    outstanding = sorted({f.code.value for f in report.advisories})
    if outstanding:
        raise NotClearedForRelease(
            report,
            "every advisory needs a signed finding resolution before a "
            "document can be released, because the release speaks for them "
            f"too. Outstanding: {', '.join(outstanding)}",
        )


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
        # Everything the writer renders, and for the same reason the scan
        # exists: it reads the markup that ships. This probe used to carry
        # the title and the cases alone, so the intro, the note, the
        # transform note and the per-sheet footer were never read — and
        # because `single_titled` suppresses the <h1> on a one-case page,
        # neither was the collection title.
        page = EvidencePageVM(
            title=result.page.title,
            intro=result.page.intro,
            note=result.page.note,
            transform_note=result.page.transform_note,
            provenance=result.page.provenance,
            mode=PresentationMode.PUBLICATION,
            approved_for_release=result.page.approved_for_release,
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
    findings.extend(verify_combined_document(result))
    return DisclosureReport(
        findings=findings,
        resolved=list(result.report.resolved),
        advisories=list(result.report.advisories),
    )


#: Where one case's markup begins in the combined document.
_CASE_SECTION_RE = re.compile(r'<section class="case" id="[^"]*">')


def verify_combined_document(result: ScrambleResult) -> list[DisclosureFinding]:
    """Scan the combined document, each case's slice with its own table.

    Per-case verification above covers a case's own markup and nothing else.
    The combined document adds the shared front matter rendered once for the
    whole collection, the kicker and ``<h1>`` branch that only a multi-case
    page takes, and the repeating per-sheet footer — which is precisely where
    the two defects this function was written alongside lived.

    One render, but never one table. Cases disagree by construction: each
    pair has its own offset, so a value case B published is routinely a value
    case A reported, and a merged table would report B's legitimate published
    date as a leak of A's. Each slice is therefore scanned with its own
    case's table, and the shared regions — the page's own prose, rewritten
    with the union — with the union table, whose ``searchable_originals``
    already drops any original that is also somebody's published value.
    """
    html = render_evidence_page(result.page)
    starts = [match.start() for match in _CASE_SECTION_RE.finditer(html)]
    if len(starts) != len(result.transforms):
        # The writer and the verifier disagree about what is on the page.
        # Scanning a misaligned slice would quietly check the wrong case.
        raise AssertionError(
            f"the combined document holds {len(starts)} case section(s) for "
            f"{len(result.transforms)} transform(s)"
        )
    findings: list[DisclosureFinding] = []
    edges = [*starts, html.rindex("</main>")]
    for index, transform in enumerate(result.transforms):
        findings.extend(
            scan_rendered_html(
                html[edges[index] : edges[index + 1]],
                transform.table,
                example_id=transform.example_id,
            )
        )
    shared = html[: edges[0]] + html[edges[-1] :]
    findings.extend(
        scan_rendered_html(shared, _union_table(result), example_id="")
    )
    return findings


def write_publication_evidence(
    result: ScrambleResult,
    output_dir: Path,
    *,
    single_document: Path | None = None,
    document_filename: str | None = EVIDENCE_DOCUMENT_FILENAME,
    verify_html: bool = True,
) -> Path:
    """Write the supplement and its public manifest, or refuse to.

    Raises :class:`PublicationBlocked` before writing anything if the
    disclosure report is not clear. Nothing is created in *output_dir* in that
    case — a blocked run leaves no artifact to be mistaken for a cleared one.

    The title, intro and note come from ``result.page`` and from nowhere
    else. They used to be parameters, and the CLI passed the *pre-transform*
    page's copies — so a collection intro naming a date, a country or a case
    reached every published page unrewritten, and unscanned, because the
    rendered scan's probe page carried no intro either. There is no caller
    for which that was the right text, so the parameters are gone rather
    than defaulted.
    """
    report = verify_rendered_output(result) if verify_html else result.report
    if report.blocked:
        raise PublicationBlocked(report)
    _check_release(result, report)

    output_dir.mkdir(parents=True, exist_ok=True)
    if single_document is not None:
        write_evidence_document(
            result.page.cases,
            single_document,
            title=result.page.title,
            intro=result.page.intro,
            note=result.page.note,
            provenance=result.page.provenance,
            mode=PresentationMode.PUBLICATION,
            approved_for_release=result.page.approved_for_release,
        )
    write_evidence_batch_html(
        result.page.cases,
        output_dir,
        index_title=result.page.title,
        collection_intro=result.page.intro,
        collection_note=result.page.note,
        provenance=result.page.provenance,
        mode=PresentationMode.PUBLICATION,
        document_filename=document_filename,
        approved_for_release=result.page.approved_for_release,
    )
    manifest = build_publication_manifest(
        result, combined_document=document_filename
    )
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

    Each subtree also holds the **combined document**, with the collection's
    own intro and note. That is not a convenience: a release is a decision
    about the whole compiled document, and the published copy of it is
    written only by a run that already cleared. A reviewer deciding whether
    to release had nowhere to read the thing they were signing for, and on a
    blocked run — which is every run before the decision is made — nowhere
    to read it at all. This directory is written unconditionally, so this is
    where it belongs.

    The publication subtree states whatever is actually true of the run,
    released or candidate. It used to show the candidate footer always, on
    the reasoning that somebody deciding whether to release should not be
    shown a document claiming to have been released — but that cannot
    happen: the footer says released only when a signed release pins this
    candidate, by which point the decision has been made. What showing the
    candidate wording regardless *did* do was put two artifacts on disk
    making contradictory claims about one document, which is the failure this
    layer polices everywhere else.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    write_evidence_batch_html(
        number_cases(internal.cases),
        output_dir / "internal",
        index_title=f"{title} — internal",
        collection_intro=internal.intro,
        collection_note=internal.note,
        provenance=internal.provenance,
        mode=PresentationMode.INTERNAL,
        document_filename=EVIDENCE_DOCUMENT_FILENAME,
    )
    released = result.page.approved_for_release
    write_evidence_batch_html(
        result.page.cases,
        output_dir / "publication",
        index_title=(
            f"{title} — {'released' if released else 'publication candidate'}"
        ),
        collection_intro=result.page.intro,
        collection_note=result.page.note,
        provenance=result.page.provenance,
        mode=PresentationMode.PUBLICATION,
        document_filename=EVIDENCE_DOCUMENT_FILENAME,
        approved_for_release=released,
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
            document_filename=EVIDENCE_DOCUMENT_FILENAME,
            release_pending=result.release is None,
            release_stale=result.release_is_stale,
            candidate_digest=result.candidate_digest,
        ),
        encoding="utf-8",
    )
    return output_dir


__all__ = [
    "MANIFEST_FILENAME",
    "REVIEW_FILENAME",
    "NotClearedForRelease",
    "PublicationBlocked",
    "PublicationTransformManifest",
    "ScramblingKeyRecord",
    "verify_combined_document",
    "verify_rendered_output",
    "write_publication_evidence",
    "write_scrambling_key",
    "write_side_by_side",
]
