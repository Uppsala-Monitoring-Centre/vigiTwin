"""The secure record: everything needed to reverse or audit the transform.

This is the artifact that makes the transform reproducible and the one that
must never travel with the supplement. It holds, per case, the offsets that
were applied and the full original → published table, which is to say it
holds the ages, dates, countries and identifiers of real reports. It belongs
in the secure environment's key store, beside the salt, and nowhere else.

What it does **not** hold is the salt. Two reasons: the salt is a credential
with a lifetime of its own, and a file that holds both the salt and the
mappings it produced is a file that can regenerate every past and future
supplement. The record names the salt by :attr:`key_id`, a keyed digest, so
an auditor can confirm which salt produced a given supplement without the
record carrying the means to do it again.

Its public counterpart is
:class:`~.manifest.PublicationTransformManifest`, which records the policy
and the shape of what was done, and no value and no offset.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from llm_dup_dev.run_manifest._git import try_dev_git_commit
from llm_dup_dev.run_manifest.builder import _detect_package_version

from .findings import DisclosureFinding
from .policy import PublicationPolicy, ReleaseApproval
from .scramble import ScrambleResult

SCHEMA_VERSION = "1.0"


class SubstitutionRecord(BaseModel):
    """One value as reported and as published. Identifying by construction."""

    model_config = ConfigDict(extra="forbid")

    original: str
    published: str
    kind: str
    note: str = ""
    #: Whether the transform moved this value. ``False`` is a legitimate
    #: outcome of a transform that ran, not a transform that failed to run —
    #: a failure is a finding, and this record would not exist for it.
    changed: bool = True


class CellRecord(BaseModel):
    """One displayed value at one place on the page, before and after.

    The substitution table is keyed by *value*; this is keyed by *place*, and
    a reviewer needs both: which values moved, and where on the page a value
    that did not move is on show.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    kind: str
    original: str
    published: str
    changed: bool


class SeparationRecord(BaseModel):
    """A day-count the page quotes, as computed and as now displayed.

    Presentation only. The finding's severity, its weight and the penalty it
    contributed to are the pipeline's and are untouched; this records that the
    *figure beside the two dates* was brought into line with them.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    assessed_days: int
    displayed_days: int


class CaseKeyRecord(BaseModel):
    """The transform applied to one case."""

    model_config = ConfigDict(extra="forbid")

    example_id: str
    pair_id: str
    position: int
    published_example_id: str
    published_example_label: str
    #: The whole-day offset applied to every complete date in the pair.
    #: ``None`` where no offset satisfied the invariants, in which case the
    #: case carries a ``date_transform_rejected`` finding and was not
    #: publishable.
    date_offset_days: int | None = None
    #: The offset applied to the clinical history, where it moved
    #: independently. Equal to ``date_offset_days`` when the two blocks were
    #: solved together. The pair of them is what a reviewer needs to undo the
    #: transform, and the reason this record belongs in the key store.
    clinical_offset_days: int | None = None
    date_candidates_tried: int = 0
    #: How many candidate offsets survived the invariants, per block, where
    #: the surviving set was smaller than the policy's threshold. ``None``
    #: means the threshold was met and the set was not counted out — see
    #: ``PublicationPolicy.date_offset_min_survivors``. A small number here is
    #: what ``date_offset_pool_thin`` reports, and it belongs in the key store
    #: because it says how much the salt was actually doing for this case.
    report_offset_survivors: int | None = None
    clinical_offset_survivors: int | None = None
    #: Keyed ``"age:years"``, ``"weight:kg"`` — one per kind and unit.
    numeric_offsets: dict[str, str] = Field(default_factory=dict)
    substitutions: list[SubstitutionRecord] = Field(default_factory=list)
    cells: list[CellRecord] = Field(default_factory=list)
    separations: list[SeparationRecord] = Field(default_factory=list)
    withheld_paths: list[str] = Field(default_factory=list)
    replaced_paths: list[str] = Field(default_factory=list)
    #: Fields a reviewer approved as the manifest wrote them. Apart from
    #: ``replaced_paths`` because the published words are the curator's, not
    #: the reviewer's -- and because these went through the substitution
    #: table, so what the page shows is not quite what the manifest says.
    approved_as_written_paths: list[str] = Field(default_factory=list)
    #: Date values in the case, and how many of them the offset moved. Equal
    #: counts mean the published page carries the pair's real timeline, which
    #: ``date_pattern_unchanged`` states outright.
    dates_total: int = 0
    dates_changed: int = 0
    date_pattern_unchanged: bool = False


class ScramblingKeyRecord(BaseModel):
    """The secure half of one publication run.

    Written by :func:`~.export.write_scrambling_key` to a path the caller
    chooses, which must be inside the secure environment.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    created_at: str
    #: Keyed digest of the salt the offsets were derived from. Reproducing the
    #: run needs the salt itself, from the secure key store.
    key_id: str
    policy_version: str
    policy: PublicationPolicy
    dev_package_version: str | None = None
    dev_git_commit: str | None = None
    cases: list[CaseKeyRecord] = Field(default_factory=list)
    #: Every finding the run produced, resolved ones included, so the record
    #: shows what a reviewer was asked and what they decided.
    findings: list[DisclosureFinding] = Field(default_factory=list)
    resolved_findings: list[DisclosureFinding] = Field(default_factory=list)
    #: The digest the candidate's content hashes to, which a release is
    #: pinned against. Here rather than in the public manifest because it is
    #: the audit side of the release, and because a published digest of the
    #: published content buys a reader nothing.
    candidate_digest: str = ""
    #: The release in force, in full — the reviewer's name, the date, the
    #: rationale and the pin. **This is where who released the document is
    #: recorded.** The public manifest carries the fact and the date only:
    #: the rationale may name real cases, exactly as a finding resolution's
    #: does, and publishing an individual's name in an automated artifact is
    #: a disclosure decision of its own.
    release: ReleaseApproval | None = None


def build_key_record(result: ScrambleResult) -> ScramblingKeyRecord:
    """The secure record for one :class:`~.scramble.ScrambleResult`."""
    return ScramblingKeyRecord(
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        key_id=result.key_id,
        policy_version=result.policy.policy_version,
        policy=result.policy,
        dev_package_version=_detect_package_version(),
        dev_git_commit=try_dev_git_commit(),
        candidate_digest=result.candidate_digest,
        release=result.release,
        cases=[
            CaseKeyRecord(
                example_id=transform.example_id,
                pair_id=transform.pair_id,
                position=transform.position,
                published_example_id=transform.published_example_id,
                published_example_label=transform.published_example_label,
                date_offset_days=transform.date_offset_days,
                clinical_offset_days=transform.clinical_offset_days,
                date_candidates_tried=transform.date_candidates_tried,
                report_offset_survivors=transform.report_offset_survivors,
                clinical_offset_survivors=transform.clinical_offset_survivors,
                numeric_offsets={
                    name: offset.describe()
                    for name, offset in sorted(transform.numeric_offsets.items())
                },
                substitutions=[
                    SubstitutionRecord(
                        original=entry.original,
                        published=entry.published,
                        kind=entry.kind,
                        note=entry.note,
                        changed=entry.changed,
                    )
                    for entry in transform.table.entries.values()
                ],
                cells=[
                    CellRecord(
                        path=outcome.path,
                        kind=outcome.kind,
                        original=outcome.original,
                        published=outcome.published,
                        changed=outcome.changed,
                    )
                    for outcome in transform.cells
                ],
                separations=[
                    SeparationRecord(
                        path=path,
                        assessed_days=assessed,
                        displayed_days=displayed,
                    )
                    for path, assessed, displayed in (
                        transform.recomputed_separations
                    )
                ],
                dates_total=transform.dates_total,
                dates_changed=transform.dates_changed,
                date_pattern_unchanged=transform.date_pattern_unchanged,
                withheld_paths=list(transform.withheld_paths),
                replaced_paths=list(transform.replaced_paths),
                approved_as_written_paths=list(
                    transform.approved_as_written_paths
                ),
            )
            for transform in result.transforms
        ],
        findings=list(result.report.findings),
        resolved_findings=list(result.report.resolved),
    )


__all__ = [
    "SCHEMA_VERSION",
    "CaseKeyRecord",
    "CellRecord",
    "SeparationRecord",
    "ScramblingKeyRecord",
    "SubstitutionRecord",
    "build_key_record",
]
