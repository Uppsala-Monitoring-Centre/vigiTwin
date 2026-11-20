"""The public record: what was done, with nothing that could undo it.

This travels with the supplement, and so is written against one rule — a
reader must be able to tell what class of transformation was applied without
being able to reverse any of it. So it records the policy, the version that
produced it, and the *bounds* the offsets were drawn from. It records no
offset, no mapping, no original value and no salt.

It used to record per-kind counts of the values transformed, aggregated over
the run. They are gone, on the grounds that they were the one field here that
gave a reader of the *manifest alone* something they could not already get
from the supplement: a reader holding the pages can see every transformed
value and very nearly count them, so the counts added nothing for them, while
for a single-case supplement the aggregate is that case's own profile and
``date:day → 16`` is close to a fingerprint. A field that is derivable where
it is useful and identifying where it is not is a field worth losing.

Recording the seed verbatim is the right thing to do for a sampling draw —
:class:`llm_dup_dev.study_eval.records.SamplingRecord` does exactly that,
because the protocol requires the randomisation to be reproducible from the
published record. Here it would be the opposite of the right thing: the
record would carry the means to reverse its own transform. Reproducibility
lives in the secure :class:`~.key.ScramblingKeyRecord` instead, and this
manifest names the salt only by its keyed digest.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from llm_dup_dev.run_manifest._git import try_dev_git_commit
from llm_dup_dev.run_manifest.builder import _detect_package_version

from ..models import PresentationMode
from .scramble import ScrambleResult

SCHEMA_VERSION = "1.0"


class CountryMaskingSummary(BaseModel):
    """Whether the countries on these pages are real.

    A reader can tell by looking — ``Country 1`` is plainly an alias and
    ``SE`` is plainly not — but a methods section needs to state the policy,
    and a reader of the manifest alone needs to know which of the two they are
    about to see. ``cases_masked`` counts how many examples were masked, since
    a single example can be decided against the default.
    """

    model_config = ConfigDict(extra="forbid")

    #: The policy's own setting, before any per-example override.
    default_masked: bool
    cases_masked: int = 0
    cases_retained: int = 0
    statement: str = ""


class SourceMaskingSummary(BaseModel):
    """Whether the shared source identifiers on these pages are real.

    Decided separately from the country, and masked by default: a named
    study or publication is a small and often public set of patients, so
    naming one beside a drug, an event and two dates narrows the field much
    further than a country does.
    """

    model_config = ConfigDict(extra="forbid")

    default_masked: bool
    cases_masked: int = 0
    cases_retained: int = 0
    statement: str = ""


class DisclosureStatus(BaseModel):
    """Where the disclosure review stood when this manifest was written."""

    model_config = ConfigDict(extra="forbid")

    #: True only when no finding was left unresolved. A published supplement
    #: should always carry ``True``; the field exists because the manifest can
    #: also be written for a blocked candidate under review.
    cleared: bool
    #: Whether a disclosure reviewer signed this document off for public
    #: sharing. **Not** :attr:`cleared`, which says only that the automated
    #: gate left no finding unresolved: a document is routinely ``cleared``
    #: and not ``approved_for_release``, which is every candidate under
    #: review. The reverse is refused — a release needs a clear gate and
    #: every advisory signed.
    approved_for_release: bool = False
    #: The date on the release, where there is one. The reviewer's *name* and
    #: rationale stay in the secure key record: the rationale may name real
    #: cases, exactly as a finding resolution's does, and publishing an
    #: individual's name in an automated artifact is a disclosure decision
    #: of its own that belongs in the manuscript.
    released_at: str | None = None
    findings_total: int = 0
    findings_resolved: int = 0
    findings_unresolved: int = 0
    #: Deliberately plain. A transform that preserved every stated
    #: relationship is not the same as an anonymised document, and a manifest
    #: that implied otherwise would be the most consequential thing on the page.
    statement: str = (
        "Displayed values were transformed for publication. This is a "
        "candidate for disclosure review, not a document certified as "
        "anonymised."
    )


class PublicationTransformManifest(BaseModel):
    """The non-sensitive record of one publication run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    created_at: str
    mode: PresentationMode = PresentationMode.PUBLICATION
    policy_version: str
    #: Keyed digest of the salt. Identifies the key without revealing it, so
    #: two supplements can be told apart and neither can be unpicked.
    key_id: str
    dev_package_version: str | None = None
    dev_git_commit: str | None = None
    cases: int = 0
    #: The range the report-date offset was drawn from, for a methods
    #: section. Bounds, not draws: an offset would be an invitation to
    #: subtract it. The lower bound is a preference rather than a guarantee —
    #: a case whose relationships survive nothing larger takes a smaller
    #: offset and says so in its findings.
    date_offset_bound_days: tuple[int, int]
    #: The range the clinical history was drawn from, where it moved
    #: independently of the report dates, and ``None`` where the two were
    #: solved together. A reader needs this to know that the interval between
    #: an event and the report describing it is not the real interval — the
    #: one quantity this scheme gives up, and not one a page can show.
    clinical_offset_bound_days: tuple[int, int] | None = None
    age_offset_bound_years: int
    measurement_offset_bound_kg: float
    #: The effective country-masking policy for this run.
    country_masking: CountryMaskingSummary
    #: The effective source-identifier policy, decided independently of it.
    source_masking: SourceMaskingSummary
    #: What the run actually transformed, in the words the footer uses.
    transform_note: str = ""
    disclosure: DisclosureStatus
    #: Filename of the combined document inside this directory, or ``None``
    #: when the run was asked not to write one. A filename and not a path:
    #: the manifest describes the directory it sits in, and the copy a run
    #: may also write elsewhere could be anywhere on disk — the reason
    #: ``ProvenanceVM.source`` is aliased rather than published.
    combined_document: str | None = None


def _country_masking(result: ScrambleResult) -> CountryMaskingSummary:
    masked = sum(1 for t in result.transforms if t.countries_masked)
    retained = len(result.transforms) - masked
    if masked and retained:
        statement = (
            "Countries are replaced by positional aliases in some examples "
            "and shown as reported in others; the labels say which."
        )
    elif masked:
        statement = (
            "Countries are replaced by positional aliases. Equality between "
            "reports and with the reporting context is preserved; the aliases "
            "carry nothing of the original."
        )
    else:
        statement = (
            "Countries are shown as reported. They are part of the reporting "
            "context the rarity was judged in, and the contextual counts "
            "belong to that country and calendar year."
        )
    return CountryMaskingSummary(
        default_masked=result.policy.mask_countries,
        cases_masked=masked,
        cases_retained=retained,
        statement=statement,
    )


def _source_masking(result: ScrambleResult) -> SourceMaskingSummary:
    masked = sum(1 for t in result.transforms if t.sources_masked)
    retained = len(result.transforms) - masked
    if masked and retained:
        statement = (
            "Shared source identifiers are replaced by aliases in some "
            "examples and shown as reported in others."
        )
    elif masked:
        statement = (
            "Shared source identifiers are replaced by aliases in their own "
            "series. Two reports that share a source still visibly share it, "
            "and the verdict says whether they describe the same case within "
            "it."
        )
    else:
        statement = "Shared source identifiers are shown as reported."
    return SourceMaskingSummary(
        default_masked=result.policy.mask_source_identifiers,
        cases_masked=masked,
        cases_retained=retained,
        statement=statement,
    )


#: Said instead, once a reviewer has signed the document off. The candidate
#: wording is the default because the candidate state is the safe one: a
#: manifest reaches this wording only through a signed, pinned release.
RELEASED_STATEMENT = (
    "Displayed values were transformed for publication. This document was "
    "cleared for public sharing by the study's disclosure reviewer."
)


def build_publication_manifest(
    result: ScrambleResult,
    *,
    combined_document: str | None = None,
) -> PublicationTransformManifest:
    """The public manifest for one :class:`~.scramble.ScrambleResult`.

    *combined_document* is passed by the writer that wrote it, so the
    manifest names the file that call produced and the two cannot drift.
    """
    report = result.report
    total = len(report.findings) + len(report.resolved)
    release = result.release
    released = result.page.approved_for_release
    return PublicationTransformManifest(
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        policy_version=result.policy.policy_version,
        key_id=result.key_id,
        dev_package_version=_detect_package_version(),
        dev_git_commit=try_dev_git_commit(),
        cases=len(result.transforms),
        date_offset_bound_days=(
            result.policy.date_offset_min_days,
            result.policy.date_offset_max_days,
        ),
        clinical_offset_bound_days=(
            (
                result.policy.clinical_offset_min_days,
                result.policy.clinical_offset_max_days,
            )
            if result.policy.decouple_clinical_dates
            else None
        ),
        age_offset_bound_years=result.policy.age_offset_max_years,
        measurement_offset_bound_kg=result.policy.measurement_offset_max_kg,
        country_masking=_country_masking(result),
        source_masking=_source_masking(result),
        transform_note=result.page.transform_note,
        combined_document=combined_document,
        disclosure=DisclosureStatus(
            cleared=not report.blocked,
            approved_for_release=released,
            released_at=release.released_at if released and release else None,
            findings_total=total,
            findings_resolved=len(report.resolved),
            findings_unresolved=len(report.findings),
            **({"statement": RELEASED_STATEMENT} if released else {}),
        ),
    )


__all__ = [
    "RELEASED_STATEMENT",
    "SCHEMA_VERSION",
    "CountryMaskingSummary",
    "DisclosureStatus",
    "SourceMaskingSummary",
    "PublicationTransformManifest",
    "build_publication_manifest",
]
