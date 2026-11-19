"""The transform itself: one built page in, one publication candidate out.

The order of work matters, and is the same for every case:

#. **Typed values first.** Every :class:`~..models.EvidenceCell` is dispatched
   on its ``kind`` through :data:`~.policy.KIND_POLICIES`. Dates are solved as
   a set, because one offset has to satisfy all of them at once; ages and
   measurements are solved per unit; countries and identifiers are aliased by
   first appearance. Each mapping is recorded in the case's substitution
   table.
#. **Aliases.** The case's own identifiers and the provenance path, replaced
   wholesale.
#. **Derived prose.** Rewritten through the table that step 1 built, so a
   finding's sentence and the table above it cannot come to disagree.
#. **Free text that needs a human.** An approved replacement, an explicit
   withhold, or a finding. Never a silent redaction.
#. **The residual scan.** On the assumption that steps 1 to 4 missed
   something.

Nothing here re-runs vigiTwin, and nothing reads a :class:`Report` or a
:class:`PairFeatures`. The input is the built view model, so every
assessment, contribution, penalty, role and classification on the page is the
one the pipeline produced — this layer cannot change a verdict because it
never sees the evidence a verdict was made from.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from llm_dup_dev.feature_generation.clinical_date_alignment import (
    min_day_difference,
)
from llm_dup_dev.models.pair_identity import split_pair_id
from llm_dup_dev.models.report import PartialDate

from ...attribution_maps import REPORT_DATE_LABEL
from ..models import (
    EvidenceCaseVM,
    EvidenceCell,
    EvidencePageVM,
    PresentationMode,
    ValueKind,
)
from .aliases import AliasBook, case_aliases, case_slug, parse_country_scope
from .approvals import is_generated_summary
from .dates import (
    DateRole,
    offset_floor,
    offset_tiers,
    parse_partial_date,
    shift_partial_date,
)
from .findings import (
    DisclosureCode,
    DisclosureFinding,
    DisclosureReport,
    partition,
)
from .invariants import (
    InvariantFailure,
    InvariantReport,
    check_cross_block,
    check_date_offset,
    dedupe_dates,
)
from .numbers import (
    NumericOffset,
    format_quantity,
    parse_quantity,
    solve_numeric_offset,
)
from .policy import (
    FREE_TEXT_FIELDS,
    KIND_POLICIES,
    FreeTextPolicy,
    PublicationApprovals,
    PublicationPolicy,
    PublicationSecrets,
    ValuePolicy,
    digest_text,
)
from .substitute import (
    MULTI_VALUE_SEPARATOR,
    SubstitutionTable,
    TextSlot,
    scan_residual,
    walk_cells,
    walk_text_slots,
)


#: Cell kinds whose values are dates.
_DATE_KINDS = frozenset(
    {
        ValueKind.REPORT_DATE,
        ValueKind.DRUG_START_DATE,
        ValueKind.DRUG_END_DATE,
        ValueKind.AE_ONSET_DATE,
    }
)


@dataclass
class CellOutcome:
    """What happened to one displayed value, at one place on the page.

    Three outcomes, and a disclosure reviewer needs to tell them apart: the
    value was transformed and moved, it was transformed and came out the same
    (a legitimate outcome, not a failure), or no transform could be applied
    and the case carries a finding saying so.
    """

    path: str
    kind: str
    original: str
    published: str

    @property
    def changed(self) -> bool:
        return self.original != self.published


@dataclass
class CaseTransform:
    """What was done to one case — the secure record of a single example."""

    example_id: str
    pair_id: str
    position: int
    published_example_id: str
    published_example_label: str
    #: The offset applied to the report dates. ``None`` where no offset
    #: satisfied the invariants, in which case no date was transformed.
    date_offset_days: int | None = None
    #: The offset applied to the clinical history — drug start and end dates
    #: and onsets. Equal to :attr:`date_offset_days` where the two blocks
    #: were solved together, which is what a policy with
    #: ``decouple_clinical_dates`` off always does.
    clinical_offset_days: int | None = None
    date_candidates_tried: int = 0
    #: How many candidate offsets survived the invariants for each block,
    #: where the search ran out of candidates before reaching the policy's
    #: threshold. ``None`` means the threshold was met and the count was not
    #: pursued further — the search stops as soon as the pool is known to be
    #: wide enough, so a large set is never counted out in full.
    report_offset_survivors: int | None = None
    clinical_offset_survivors: int | None = None
    #: Whether this case's countries were replaced by aliases. The policy's
    #: default unless an override decided otherwise, and recorded either way:
    #: a retained country is a deliberate choice a reviewer must see, not an
    #: omission.
    countries_masked: bool = False
    #: Whether this case's shared source identifiers were replaced by
    #: aliases. Decided independently of the country.
    sources_masked: bool = True
    numeric_offsets: dict[str, NumericOffset] = field(default_factory=dict)
    table: SubstitutionTable = field(default_factory=SubstitutionTable)
    findings: list[DisclosureFinding] = field(default_factory=list)
    advisories: tuple[InvariantFailure, ...] = ()
    withheld_paths: list[str] = field(default_factory=list)
    replaced_paths: list[str] = field(default_factory=list)
    #: Fields published as the manifest wrote them, on a reviewer's
    #: signature. Recorded apart from ``replaced_paths`` because the two say
    #: different things: one is a reviewer's own words, the other is the
    #: curator's, approved and then rewritten through the substitution table.
    approved_as_written_paths: list[str] = field(default_factory=list)
    #: Every cell the transform touched, with what it came out as.
    cells: list[CellOutcome] = field(default_factory=list)
    #: Separations the page quotes, recomputed from the transformed dates.
    #: ``(path, original_days, published_days)``.
    recomputed_separations: list[tuple[str, int, int]] = field(
        default_factory=list
    )

    @property
    def dates_changed(self) -> int:
        return sum(
            1
            for entry in self.table.entries.values()
            if entry.kind.startswith("date:") and entry.changed
        )

    @property
    def dates_total(self) -> int:
        return sum(
            1
            for entry in self.table.entries.values()
            if entry.kind.startswith("date:")
        )

    @property
    def date_pattern_unchanged(self) -> bool:
        """Whether every date in the case is published as reported.

        True when the accepted offset moved nothing — an offset of zero, or
        one too small to move a case of year-only dates. The transform allows
        it on purpose, and a reviewer has to be told, because the published
        page then carries the pair's real timeline.
        """
        return self.dates_total > 0 and self.dates_changed == 0


@dataclass
class ScrambleResult:
    """A publication candidate, and everything a reviewer needs to judge it.

    :attr:`page` is safe to publish **only** when :attr:`report` is not
    blocked. The writers in :mod:`.export` enforce that; this object does not,
    because a side-by-side review wants the page even when it is blocked.
    """

    page: EvidencePageVM
    report: DisclosureReport
    transforms: list[CaseTransform]
    key_id: str
    policy: PublicationPolicy

    @property
    def blocked(self) -> bool:
        return self.report.blocked


def _components(text: str) -> list[str]:
    """A cell's text as the separate values the builder joined."""
    return [part for part in text.split(MULTI_VALUE_SEPARATOR)]


def _rejoin(parts: list[str]) -> str:
    return MULTI_VALUE_SEPARATOR.join(parts)


def _transformable(cell: EvidenceCell) -> bool:
    return bool(cell.text.strip()) and not cell.missing and not cell.withheld


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


@dataclass
class _ContextGuard:
    """What the reporting context's year has to go on naming.

    The contextual frequency counts are real: they were computed in one
    country and one calendar year, and those counts are published unchanged.
    The context label is what tells a reader which population they belong
    to, so the year in it is **never shifted** — a shifted year would
    reattribute real counts to a year they did not come from, which is a
    claim the artifact has no business making and, where the country is
    retained, one a reader could check and find false.

    Freezing the year is only half of it. ``reporting_context`` derives the
    year as the first year *shared by both reports' report dates*
    (``_extract_years``), so an offset that carries either report's date over
    a new year leaves the page naming a country-year its own displayed dates
    no longer support. Hence :attr:`left_report_dates` and
    :attr:`right_report_dates`: the year has to remain shared by both after
    the shift, which binds the offset to the distance from the nearer report
    date to its year boundary.
    """

    year: int | None = None
    left_report_dates: tuple[PartialDate, ...] = ()
    right_report_dates: tuple[PartialDate, ...] = ()

    def holds_after(self, offset_days: int) -> bool:
        """Whether the context year is still shared after shifting by *offset*."""
        if self.year is None:
            return True

        def years(dates: tuple[PartialDate, ...]) -> set[int]:
            return {
                int(shift_partial_date(d, offset_days).value[:4]) for d in dates
            }

        shared_before = years_of(self.left_report_dates) & years_of(
            self.right_report_dates
        )
        if self.year not in shared_before:
            # The artifacts did not agree about the year in the first place;
            # there is nothing for the shift to break.
            return True
        return self.year in (
            years(self.left_report_dates) & years(self.right_report_dates)
        )


def years_of(dates: tuple[PartialDate, ...]) -> set[int]:
    """The calendar years *dates* fall in, as written."""
    return {int(d.value[:4]) for d in dates}


@dataclass
class _DateSet:
    """Every date in a case, with the subsets the invariants need."""

    dates: list[PartialDate] = field(default_factory=list)
    #: The same dates, split by what they record. A report date says when a
    #: report reached the centre and is pinned near its context year; a
    #: clinical date says when something happened to the patient and is not.
    report_dates: list[PartialDate] = field(default_factory=list)
    clinical_dates: list[PartialDate] = field(default_factory=list)
    #: The clinical dates again, keyed by whose column they are shown in —
    #: ``"left"``, ``"right"``, or ``""`` for a cell shown once. The guard
    #: that lets the clinical block move forwards is per report: an event
    #: stays before the report date of *its own* report.
    clinical_by_side: dict[str, list[PartialDate]] = field(default_factory=dict)
    #: Pairs a finding states a numeric separation between.
    asserted: list[tuple[PartialDate, PartialDate]] = field(default_factory=list)
    #: True when a finding asserts a separation *between* the two blocks. Its
    #: figure has to survive exactly, which only one shared offset can
    #: promise, so such a case takes a single offset however the policy is
    #: set.
    coupled: bool = False
    #: Reporting-context years. Frozen, so they are kept out of the
    #: relational set entirely and checked by :class:`_ContextGuard` instead.
    context_years: list[int] = field(default_factory=list)
    guard: _ContextGuard = field(default_factory=_ContextGuard)


def _collect_dates(case: EvidenceCaseVM, transform: CaseTransform) -> _DateSet:
    """Every displayed date, and the pairs a finding asserts a gap between."""
    dates: list[PartialDate] = []
    report_dates: list[PartialDate] = []
    clinical_dates: list[PartialDate] = []
    clinical_by_side: dict[str, list[PartialDate]] = {}
    context_years: list[int] = []
    for path, cell in walk_cells(case):
        if cell.kind not in _DATE_KINDS or not _transformable(cell):
            continue
        for component in _components(cell.text):
            parsed = parse_partial_date(component)
            if parsed is not None:
                if cell.kind is ValueKind.REPORT_DATE:
                    report_dates.append(parsed)
                else:
                    clinical_dates.append(parsed)
                    clinical_by_side.setdefault(_side_of(path), []).append(
                        parsed
                    )
            if parsed is None:
                transform.findings.append(
                    DisclosureFinding(
                        code=DisclosureCode.DATE_UNPARSEABLE,
                        example_id=transform.example_id,
                        field_path=path,
                        message=(
                            f"{component!r} is shown as a {cell.kind.value} "
                            "but is not an ISO date, so it cannot be shifted"
                        ),
                    )
                )
                continue
            dates.append(parsed)

    # The reporting context's year does not join the relational set: it is
    # frozen, so comparing its interval against dates that moved would report
    # changes to relationships that no longer exist.
    for _, cell in walk_cells(case):
        if cell.kind is not ValueKind.COUNTRY or not _transformable(cell):
            continue
        for component in _components(cell.text):
            scope = parse_country_scope(component)
            if scope is not None and scope.year is not None:
                context_years.append(scope.year)

    asserted: list[tuple[PartialDate, PartialDate]] = []
    coupled = False
    for discrepancy in case.discrepancies:
        left, right = discrepancy.left, discrepancy.right
        if left is None or right is None:
            continue
        if left.kind not in _DATE_KINDS or right.kind not in _DATE_KINDS:
            continue
        a = parse_partial_date(left.text)
        b = parse_partial_date(right.text)
        if a is not None and b is not None:
            asserted.append((a, b))
            if (left.kind is ValueKind.REPORT_DATE) != (
                right.kind is ValueKind.REPORT_DATE
            ):
                coupled = True

    left_dates, right_dates = _report_date_values(case)
    return _DateSet(
        dates=dedupe_dates(dates),
        report_dates=dedupe_dates(report_dates),
        clinical_dates=dedupe_dates(clinical_dates),
        clinical_by_side={
            side: dedupe_dates(values)
            for side, values in clinical_by_side.items()
        },
        coupled=coupled,
        asserted=asserted,
        context_years=list(dict.fromkeys(context_years)),
        guard=_ContextGuard(
            year=context_years[0] if context_years else None,
            left_report_dates=left_dates,
            right_report_dates=right_dates,
        ),
    )


def _side_of(path: str) -> str:
    """``"left"``, ``"right"``, or ``""`` for a cell shown once.

    The view model names a report's own column in the field that holds it —
    ``left_entries``, ``right_term``, ``left`` — so the path a cell was
    reached by says which report it belongs to. A cell outside any column
    (a drug named once, shared by both) has no side, and is checked against
    both reports rather than neither.
    """
    for segment in path.split("."):
        name = segment.split("[")[0]
        if name == "left" or name.startswith("left_"):
            return "left"
        if name == "right" or name.startswith("right_"):
            return "right"
    return ""


def _report_date_values(
    case: EvidenceCaseVM,
) -> tuple[tuple[PartialDate, ...], tuple[PartialDate, ...]]:
    """Each report's own report dates, as the characteristics table shows them.

    The two sides matter separately: the reporting context's year is the one
    the two reports *share*, so checking a merged set would accept an offset
    that moved one report out of the year while the other stayed.
    """

    def parse(text: str) -> tuple[PartialDate, ...]:
        found = (parse_partial_date(c) for c in _components(text))
        return tuple(d for d in found if d is not None)

    for row in case.characteristics:
        if row.label != REPORT_DATE_LABEL:
            continue
        return parse(row.left.text), parse(row.right.text)
    return (), ()


@dataclass
class _OffsetSearch:
    """What one search over one block's candidates came back with."""

    offset: int | None = None
    advisories: tuple[InvariantFailure, ...] = ()
    failures: tuple[InvariantFailure, ...] = ()
    tried: int = 0
    #: How many candidates were found to survive the invariants. Exact only
    #: when :attr:`thin` — the search stops counting at the policy's
    #: threshold, because the question a reviewer needs answered is "is this
    #: set small enough to enumerate", not "how large is it".
    survivors: int = 0
    #: True when the accepted tier ran out of candidates before the threshold
    #: was reached, so :attr:`survivors` is the whole surviving set.
    thin: bool = False


def _context_year_failure(guard: _ContextGuard) -> InvariantFailure:
    return InvariantFailure(
        "country_year_population",
        f"the reporting context names {guard.year}, and this offset carries a "
        "report date into another calendar year, so the page would attribute "
        "real contextual counts to a country-year its own dates do not "
        "support",
    )


def _search_offset(
    dates: list[PartialDate],
    tiers: list[list[int]],
    policy: PublicationPolicy,
    *,
    asserted: Sequence[tuple[PartialDate, PartialDate]] = (),
    guard: _ContextGuard | None = None,
    cross: Callable[[int], InvariantReport] | None = None,
) -> _OffsetSearch:
    """The first candidate that preserves everything this block says.

    Two passes over the same candidate order. The first accepts only an
    offset that raises no advisory within the block; the second accepts any
    offset that passes the invariants. So an offset that leaves every
    relationship intact is preferred to one that merely leaves the stated
    ones intact, without either becoming a requirement, and the order is the
    same in both passes so the choice stays reproducible from the seed.

    The passes run *within* each band of :func:`~.dates.offset_tiers`, so a
    stronger offset that moves an unstated relationship is preferred to a
    weaker one that moves nothing. Obfuscation first, fidelity second: the
    other order would answer a disclosure question with a presentational
    preference.

    The accepted candidate is the first one in trial order, exactly as
    before. What is new is that the scan does not stop there: it keeps going
    until it has seen
    :attr:`~.policy.PublicationPolicy.date_offset_min_survivors` survivors,
    so the result can say whether the set the offset was drawn from is small
    enough for a reader to enumerate. It stops at the threshold rather than
    counting the set out, because the question is only whether the pool is
    wide enough — and a wide pool is the common case, so counting it in full
    would be work for an answer nobody reads.
    """
    found = _OffsetSearch()
    wanted = max(policy.date_offset_min_survivors, 1)
    for tier in tiers:
        for clean_only in (True, False):
            accepted: list[int] = []
            for candidate in tier:
                found.tried += 1
                report = check_date_offset(
                    dates, candidate, policy, asserted=asserted
                )
                if not report.accepted:
                    found.failures = report.failures
                    continue
                if clean_only and report.advisories:
                    continue
                if guard is not None and not guard.holds_after(candidate):
                    found.failures = (_context_year_failure(guard),)
                    continue
                if cross is not None:
                    verdict = cross(candidate)
                    if not verdict.accepted:
                        found.failures = verdict.failures
                        continue
                if not accepted:
                    found.advisories = report.advisories
                accepted.append(candidate)
                if len(accepted) >= wanted:
                    break
            if accepted:
                found.offset = accepted[0]
                found.survivors = len(accepted)
                found.thin = len(accepted) < wanted
                return found
    return found


def _note_weak_offset(
    transform: CaseTransform,
    policy: PublicationPolicy,
    offset: int,
    role: DateRole,
) -> None:
    """Say so when the only offsets that worked were below the floor.

    Advisory, not blocking: the dates did move, so the page does not carry
    the real timeline. What it carries is one close to it, and a reviewer
    looking at a case with a long history should know which.
    """
    floor = offset_floor(policy, role)
    if offset == 0 or floor <= 1 or abs(offset) >= floor:
        return
    transform.findings.append(
        DisclosureFinding(
            code=DisclosureCode.DATE_OFFSET_BELOW_FLOOR,
            example_id=transform.example_id,
            message=(
                f"the {role.value} dates moved {offset:+d} days, short of the "
                f"policy floor of {floor}: no larger offset preserved what "
                "this case's dates say about each other"
            ),
        )
    )


def _note_thin_pool(
    transform: CaseTransform,
    policy: PublicationPolicy,
    search: _OffsetSearch,
    role: DateRole,
) -> None:
    """Say so when the accepted offset came from a set a reader can enumerate.

    Advisory, like :func:`_note_weak_offset`: the offset is sound and nothing
    has leaked. What a reviewer is being told is that the *secret* did less
    work than the policy intends. The salt chooses within the surviving set;
    it does nothing to hide the set itself, which anyone holding this source
    and the published page can recompute by running the invariants over the
    published dates. A case with two survivors is a coin toss at the real
    timeline however good the salt is.

    This is the cost of publishing the method, booked where the rest of the
    layer books its weaknesses — in a finding a human has to clear, rather
    than in a paragraph of documentation nobody reads at review time.
    """
    if not search.thin or policy.date_offset_min_survivors <= 0:
        return
    transform.findings.append(
        DisclosureFinding(
            code=DisclosureCode.DATE_OFFSET_POOL_THIN,
            example_id=transform.example_id,
            message=(
                f"only {search.survivors} candidate offset(s) preserved what "
                f"this case's {role.value} dates say about each other, against "
                f"a policy threshold of {policy.date_offset_min_survivors}. "
                "The set is small enough to enumerate from the published page, "
                "so the real timeline is one of a few; weigh that before "
                "publishing this case"
            ),
            detail=(
                f"{search.survivors} survivor(s) of "
                f"{search.tried} candidate(s) tried"
            ),
        )
    )


def _cross_block_check(
    date_set: _DateSet, clinical_offset: int, report_offset: int
) -> InvariantReport:
    """Each report's clinical dates against that report's own report dates.

    Per report, because that is the relationship the page states and the one
    a reader would act on: this event happened, and then this report
    described it. A clinical date from the other report has no stated
    relationship to this report's date, and holding it to one would reject
    offsets for no reader's benefit.

    A date shown in neither column — a drug named once because both reports
    carry it — is checked against both reports, not neither.
    """
    failures: list[InvariantFailure] = []
    for side, own in (
        ("left", date_set.guard.left_report_dates),
        ("right", date_set.guard.right_report_dates),
    ):
        clinical = date_set.clinical_by_side.get(side, [])
        if not clinical:
            continue
        failures.extend(
            check_cross_block(
                clinical,
                list(own) or date_set.report_dates,
                clinical_offset,
                report_offset,
                label=f"the {side} report",
            ).failures
        )
    shared = date_set.clinical_by_side.get("", [])
    if shared:
        failures.extend(
            check_cross_block(
                shared,
                date_set.report_dates,
                clinical_offset,
                report_offset,
                label="either report",
            ).failures
        )
    return InvariantReport(failures=tuple(failures))


def _solve_date_offsets(
    case: EvidenceCaseVM,
    date_set: _DateSet,
    policy: PublicationPolicy,
    secrets: PublicationSecrets,
    transform: CaseTransform,
) -> tuple[int | None, int | None]:
    """An offset for the report dates and one for the clinical history.

    The two are solved separately because they are independent in the source
    and differently constrained in the page. A report date is pinned near the
    reporting context's year; a clinical date is not pinned at all, and a case
    mixing year-, month- and day-precision clinical dates can usually only
    move by whole years. Asking one offset to be both a few days and a whole
    year is how a sixteen-date history came to be published exactly as
    reported: zero was the only value that satisfied both.

    Falls back to a single offset for everything when the policy says so,
    when a finding asserts a separation across the two blocks, when the case
    has only one kind of date, or when the split search finds nothing — the
    single search is the broader one, so trying it after a failed split
    costs a case nothing.
    """
    if not date_set.dates:
        return None, None

    def seed(role: str) -> str:
        return secrets.seed(policy.policy_version, "dates", role, case.pair_id)

    tried = 0
    split_failures: tuple[InvariantFailure, ...] = ()
    if (
        policy.decouple_clinical_dates
        and not date_set.coupled
        and date_set.report_dates
        and date_set.clinical_dates
    ):
        reports = _search_offset(
            date_set.report_dates,
            offset_tiers(policy, seed("report"), role=DateRole.REPORT),
            policy,
            asserted=date_set.asserted,
            guard=date_set.guard,
        )
        tried += reports.tried
        if reports.offset is None:
            split_failures = reports.failures
        else:
            chosen = reports.offset
            clinical = _search_offset(
                date_set.clinical_dates,
                offset_tiers(
                    policy, seed("clinical"), role=DateRole.CLINICAL
                ),
                policy,
                asserted=date_set.asserted,
                cross=lambda candidate: _cross_block_check(
                    date_set, candidate, chosen
                ),
            )
            tried += clinical.tried
            if clinical.offset is not None:
                transform.advisories = reports.advisories + clinical.advisories
                transform.date_candidates_tried = tried
                if reports.thin:
                    transform.report_offset_survivors = reports.survivors
                if clinical.thin:
                    transform.clinical_offset_survivors = clinical.survivors
                _note_weak_offset(
                    transform, policy, reports.offset, DateRole.REPORT
                )
                _note_weak_offset(
                    transform, policy, clinical.offset, DateRole.CLINICAL
                )
                _note_thin_pool(transform, policy, reports, DateRole.REPORT)
                _note_thin_pool(transform, policy, clinical, DateRole.CLINICAL)
                return reports.offset, clinical.offset
            split_failures = clinical.failures

    single = _search_offset(
        date_set.dates,
        offset_tiers(policy, seed("all"), role=DateRole.REPORT),
        policy,
        asserted=date_set.asserted,
        guard=date_set.guard,
    )
    tried += single.tried
    transform.date_candidates_tried = tried
    if single.offset is not None:
        transform.advisories = single.advisories
        if single.thin:
            # One offset governs both blocks, so the surviving set is the same
            # set for each and is recorded against both.
            transform.report_offset_survivors = single.survivors
            transform.clinical_offset_survivors = single.survivors
        _note_weak_offset(transform, policy, single.offset, DateRole.REPORT)
        _note_thin_pool(transform, policy, single, DateRole.REPORT)
        return single.offset, single.offset

    transform.findings.append(
        DisclosureFinding(
            code=DisclosureCode.DATE_TRANSFORM_REJECTED,
            example_id=transform.example_id,
            message=(
                "no offset preserved the relationships this case's dates "
                "carry, with the report dates and the clinical history "
                "solved together or apart; the example needs manual handling"
            ),
            detail="; ".join(
                f.detail for f in (single.failures or split_failures)
            ),
        )
    )
    return None, None


#: ``", at least 796 days apart"`` as ``build._date_finding_tail`` writes it.
#: Narrow on purpose: the only figure on the page derived from two dates
#: rather than displayed as one.
_SEPARATION_RE = re.compile(
    r"(?P<lead>, at least )(?P<days>\d+)(?P<space> )(?P<unit>days?)"
    r"(?P<trail> apart)"
)


def _requote_separation(text: str, days: int) -> tuple[str, int | None]:
    """*text* with its quoted separation replaced by *days*.

    Returns the rewritten text and the figure that was there before, or
    ``None`` where the text quotes no separation at all.
    """
    match = _SEPARATION_RE.search(text)
    if match is None:
        return text, None
    unit = "day" if days == 1 else "days"
    rewritten = (
        text[: match.start()]
        + f"{match.group('lead')}{days} {unit}{match.group('trail')}"
        + text[match.end() :]
    )
    return rewritten, int(match.group("days"))


def _recompute_separations(
    case: EvidenceCaseVM, transform: CaseTransform
) -> None:
    """Rewrite each quoted separation from the dates now on the page.

    A finding saying two dates are "at least 51 days apart" has to agree with
    the two dates printed beside it, and the tolerance in :mod:`.invariants`
    means the transformed separation may differ from the figure the pipeline
    computed. So the figure is recomputed here — **for presentation only**.
    The finding's severity, its weight, the penalty it contributed to and
    every count in the assessment stay exactly as the pipeline produced them;
    nothing in this function touches them, and it could not reach them if it
    tried.

    Each rewrite is recorded on the transform, so the side-by-side review can
    show the reviewer which figure moved and by how much.
    """
    for index, discrepancy in enumerate(case.discrepancies):
        left, right = discrepancy.left, discrepancy.right
        if left is None or right is None:
            continue
        a = parse_partial_date(left.text)
        b = parse_partial_date(right.text)
        if a is None or b is None:
            continue
        published = min_day_difference(a, b)

        quoted: int | None = None
        discrepancy.tail, was = _requote_separation(discrepancy.tail, published)
        quoted = was if was is not None else quoted
        discrepancy.text, was = _requote_separation(discrepancy.text, published)
        quoted = was if was is not None else quoted
        discrepancy.short_text, was = _requote_separation(
            discrepancy.short_text, published
        )
        quoted = was if was is not None else quoted
        if quoted is not None and quoted != published:
            transform.recomputed_separations.append(
                (f"discrepancies[{index}]", quoted, published)
            )


def _register_dates(
    dates: list[PartialDate], offset: int, table: SubstitutionTable
) -> None:
    for original in dates:
        moved = shift_partial_date(original, offset)
        table.add(
            original.value,
            moved.value,
            kind=f"date:{original.resolution}",
            note=f"{offset:+d} days",
        )


# ---------------------------------------------------------------------------
# Ages and measurements
# ---------------------------------------------------------------------------

_NUMERIC_KINDS: dict[ValueKind, tuple[str, bool, DisclosureCode, DisclosureCode]] = {
    ValueKind.AGE: (
        "age",
        True,
        DisclosureCode.AGE_UNPARSEABLE,
        DisclosureCode.AGE_TRANSFORM_REJECTED,
    ),
    ValueKind.HEIGHT: (
        "height",
        False,
        DisclosureCode.MEASUREMENT_UNPARSEABLE,
        DisclosureCode.MEASUREMENT_TRANSFORM_REJECTED,
    ),
    ValueKind.WEIGHT: (
        "weight",
        False,
        DisclosureCode.MEASUREMENT_UNPARSEABLE,
        DisclosureCode.MEASUREMENT_TRANSFORM_REJECTED,
    ),
}


def _transform_numeric(
    case: EvidenceCaseVM,
    kind: ValueKind,
    policy: PublicationPolicy,
    secrets: PublicationSecrets,
    transform: CaseTransform,
) -> None:
    """Solve and register one numeric kind, grouped by unit."""
    label, is_age, unparseable, rejected = _NUMERIC_KINDS[kind]
    groups: dict[str, list[str]] = {}
    for path, cell in walk_cells(case):
        if cell.kind is not kind or not _transformable(cell):
            continue
        for component in _components(cell.text):
            quantity = parse_quantity(component)
            if quantity is None:
                transform.findings.append(
                    DisclosureFinding(
                        code=unparseable,
                        example_id=transform.example_id,
                        field_path=path,
                        message=(
                            f"{component!r} is shown as a {label} but is not a "
                            "figure with a unit, so it cannot be offset"
                        ),
                    )
                )
                continue
            groups.setdefault(quantity.unit, []).append(component)

    for unit, components in sorted(groups.items()):
        # Deduplicated, first-appearance order: two reports stating the same
        # age state one value, and the group is about distinct values.
        unique = list(dict.fromkeys(components))
        quantities = [parse_quantity(c) for c in unique]
        values = [q for q in quantities if q is not None]
        seed = secrets.seed(policy.policy_version, label, unit, case.pair_id)
        solution = solve_numeric_offset(values, policy, seed, is_age=is_age)
        if solution.offset is None:
            transform.findings.append(
                DisclosureFinding(
                    code=rejected,
                    example_id=transform.example_id,
                    message=(
                        f"no {label} offset in {unit} among "
                        f"{solution.candidates_tried} candidates "
                        f"({solution.pool_description}) preserved this pair's "
                        "differences, match grid and bands"
                    ),
                    detail="; ".join(f.detail for f in solution.failures),
                )
            )
            continue
        transform.numeric_offsets[f"{label}:{unit}"] = solution.offset
        for component, moved in zip(unique, solution.values, strict=True):
            table_value = format_quantity(moved)
            transform.table.add(
                component,
                table_value,
                kind=label,
                note=solution.offset.describe(),
            )


# ---------------------------------------------------------------------------
# Countries and identifiers
# ---------------------------------------------------------------------------


def _transform_countries(
    case: EvidenceCaseVM,
    book: AliasBook,
    *,
    mask: bool,
    transform: CaseTransform,
) -> None:
    """Alias each country, or record that it was deliberately retained.

    The year inside a composite label is frozen either way: see
    :class:`_ContextGuard`. It is still registered as an unchanged mapping,
    which keeps it out of the residual scan's date-shape check — an
    untransformed year on the page is a leak, a *deliberately* untransformed
    one is the reporting context, and the table is how the scan tells them
    apart.
    """
    for path, cell in walk_cells(case):
        if cell.kind is not ValueKind.COUNTRY or not _transformable(cell):
            continue
        for component in _components(cell.text):
            scope = parse_country_scope(component)
            if scope is None:
                # A reporting *level*, which names nobody. Left as it is.
                continue
            if not scope.country:  # pragma: no cover - parser guarantees one
                transform.findings.append(
                    DisclosureFinding(
                        code=DisclosureCode.COUNTRY_UNPARSEABLE,
                        example_id=transform.example_id,
                        field_path=path,
                        message=f"{component!r} has no country to mask",
                    )
                )
                continue
            published = (
                book.country(scope.country) if mask else scope.country
            )
            note = (
                "masked" if mask else "retained: mask_countries is off"
            )
            transform.table.add(
                scope.country, published, kind="country", note=note
            )
            if scope.year is not None:
                transform.table.add(
                    f"{scope.year:04d}",
                    f"{scope.year:04d}",
                    kind="date:year",
                    note="reporting context year, not shifted",
                )
            transform.table.add(
                component,
                scope.rebuild(published, scope.year),
                kind="country",
                note=f"reporting context, {note}",
            )


def _transform_sources(
    case: EvidenceCaseVM,
    book: AliasBook,
    *,
    mask: bool,
    transform: CaseTransform,
) -> None:
    """Alias each shared source identifier, or record that it was retained.

    Decided separately from the country: a named study or publication is a
    small and often public set of patients, so naming it beside a drug, an
    event and two dates narrows the field much further than a country does.
    Hence masked by default, where the country is not.
    """
    for _, cell in walk_cells(case):
        if cell.kind is not ValueKind.SOURCE_IDENTIFIER:
            continue
        if not _transformable(cell):
            continue
        for component in _components(cell.text):
            transform.table.add(
                component,
                book.source(component) if mask else component,
                kind="source_identifier",
                note=(
                    "masked"
                    if mask
                    else "retained: mask_source_identifiers is off"
                ),
            )


def _parse_count(text: str) -> int | None:
    """``"1,234"`` as an int, or ``None`` where the cell holds no figure.

    The frequency table's counts are pre-formatted by the builder, so this
    reverses exactly that formatting and nothing else: anything it does not
    recognise is left for a human rather than guessed at.
    """
    value = text.strip().replace(",", "")
    return int(value) if value.isdigit() else None


def _note_retained_country_small_count(
    case: EvidenceCaseVM, policy: PublicationPolicy, transform: CaseTransform
) -> None:
    """Block where a real country is shown beside a tiny contextual count.

    The counts are retained by instruction and the country is retained by
    default, so neither value is a defect and no transform here will touch
    them. What makes the pair of them a disclosure question is that **the
    counts include the two reports under review**: a co-occurrence count of
    two in a named country and calendar year is this pair and nobody else,
    and someone with database access can work back from it.

    Only scopes that *name a country* are considered — the global row is
    judged in a population the page does not identify, and
    :func:`~.aliases.parse_country_scope` is what tells the two apart, the
    same function the country transform uses. A case whose countries were
    masked is not reported at all: the aliases have already removed the half
    of the combination that narrows it.
    """
    if policy.min_contextual_count <= 0 or transform.countries_masked:
        return
    worst: tuple[int, str, str] | None = None
    for row in case.freq_rows:
        scope = parse_country_scope(row.scope_label)
        if scope is None or not scope.country:
            continue
        for column, text in (
            ("drugs", row.n_drugs),
            ("AEs", row.n_aes),
            ("both", row.n_both),
        ):
            count = _parse_count(text)
            if count is None or count > policy.min_contextual_count:
                continue
            if worst is None or count < worst[0]:
                worst = (count, row.scope_label, column)
    if worst is None:
        return
    count, scope_label, column = worst
    transform.findings.append(
        DisclosureFinding(
            code=DisclosureCode.RETAINED_COUNTRY_SMALL_COUNT,
            example_id=transform.example_id,
            message=(
                f"the {scope_label!r} scope is shown as reported and its "
                f"{column} count is {count}, at or below the policy's "
                f"threshold of {policy.min_contextual_count}. The counts "
                "include the two reports under review, so this names a "
                "population of a few people in a real country and year. "
                "Mask this example's country with a CountryMaskingOverride, "
                "or resolve this finding with a reason the population is "
                "wide enough"
            ),
            detail=(
                f"scope {scope_label!r}, {column} count {count}, threshold "
                f"{policy.min_contextual_count}"
            ),
        )
    )


def _transform_identifiers(
    case: EvidenceCaseVM, book: AliasBook, transform: CaseTransform
) -> None:
    for _, cell in walk_cells(case):
        if cell.kind is not ValueKind.IDENTIFIER or not _transformable(cell):
            continue
        for component in _components(cell.text):
            transform.table.add(
                component, book.identifier(component), kind="identifier"
            )


# ---------------------------------------------------------------------------
# One case
# ---------------------------------------------------------------------------


def _apply_cells(
    case: EvidenceCaseVM,
    transform: CaseTransform,
    approvals: PublicationApprovals,
) -> None:
    """Write every cell's published text, and honour withhold decisions.

    Each transformed cell is recorded as a :class:`CellOutcome`, whether or
    not its value moved. A value that came back unchanged is a legitimate
    outcome and has to be distinguishable from one no transform reached, so
    the record is of the transform having been *applied*, not of the value
    having *changed*.
    """
    deferred: list[tuple[str, EvidenceCell]] = []
    for path, cell in walk_cells(case):
        # ``transform.example_id`` and not ``case.example_id``: the alias pass
        # overwrites the latter, and an approval is written against the
        # internal review's own identifier.
        approved = approvals.text_for(transform.example_id, path)
        if approved is not None and approved.withhold:
            cell.text = ""
            cell.withheld = True
            transform.withheld_paths.append(path)
            continue
        if not _transformable(cell):
            continue
        policy = KIND_POLICIES.get(cell.kind)
        if policy is None:  # pragma: no cover - exhaustiveness is tested
            transform.findings.append(
                DisclosureFinding(
                    code=DisclosureCode.UNSUPPORTED_VALUE_KIND,
                    example_id=transform.example_id,
                    field_path=path,
                    message=(
                        f"{cell.kind.value!r} has no publication policy, so "
                        "its value cannot be published"
                    ),
                )
            )
            continue
        if policy is ValuePolicy.KEEP:
            continue
        if policy is ValuePolicy.REVIEW_FREE_TEXT:
            # Prose in a cell: rewritten once the table is complete.
            deferred.append((path, cell))
            continue
        original = cell.text
        cell.text = _rejoin(
            [
                transform.table.published_for(component) or component
                for component in _components(original)
            ]
        )
        transform.cells.append(
            CellOutcome(
                path=path,
                kind=cell.kind.value,
                original=original,
                published=cell.text,
            )
        )
    for path, cell in deferred:
        original = cell.text
        cell.text = transform.table.rewrite(original)
        transform.cells.append(
            CellOutcome(
                path=path,
                kind=cell.kind.value,
                original=original,
                published=cell.text,
            )
        )


def _apply_text(
    case: EvidenceCaseVM,
    transform: CaseTransform,
    policy: PublicationPolicy,
    approvals: PublicationApprovals,
) -> None:
    """Rewrite derived prose; resolve or flag everything a human owns."""
    for slot in walk_text_slots(case):
        if slot.key == "EvidenceCell.text":
            continue  # handled by the cell pass
        rule = FREE_TEXT_FIELDS.get(slot.key)
        if rule is None:  # pragma: no cover - exhaustiveness is tested
            transform.findings.append(
                DisclosureFinding(
                    code=DisclosureCode.UNSUPPORTED_TEXT_FIELD,
                    example_id=transform.example_id,
                    field_path=slot.path,
                    message=(
                        f"{slot.key} has no free-text policy, so its contents "
                        "cannot be published"
                    ),
                )
            )
            continue
        if rule is FreeTextPolicy.PASS_THROUGH:
            continue
        if rule is FreeTextPolicy.ALIAS:
            _apply_alias(slot, transform, policy)
            continue
        value = slot.read()
        if rule is FreeTextPolicy.SUBSTITUTE:
            if isinstance(value, list):
                slot.write([transform.table.rewrite(item) for item in value])
            elif value:
                slot.write(transform.table.rewrite(value))
            continue
        _apply_review(slot, transform, policy, approvals)

    # A block whose paragraphs were all withheld has nothing left to render.
    case.narrative_blocks = [
        block for block in case.narrative_blocks if block.paragraphs
    ]


def _apply_alias(
    slot: TextSlot, transform: CaseTransform, policy: PublicationPolicy
) -> None:
    """Replace an identifier or a path with something that carries none of it."""
    if slot.key == "EvidenceCaseVM.pair_id":
        slot.write(transform.published_example_id)
    elif slot.key == "EvidenceCaseVM.example_id":
        slot.write(transform.published_example_id)
    elif slot.key == "EvidenceCaseVM.example_label":
        slot.write(transform.published_example_label)
    elif slot.key == "ProvenanceVM.source":
        slot.write(policy.provenance_source_label if slot.read() else "")
    transform.replaced_paths.append(slot.path)


_REVIEW_CODES = {
    "EvidenceTextVM.paragraphs": DisclosureCode.HUMAN_TEXT_REVIEW_REQUIRED,
    "CharacteristicRow.left_evidence": (
        DisclosureCode.NARRATIVE_SNIPPET_REVIEW_REQUIRED
    ),
    "CharacteristicRow.right_evidence": (
        DisclosureCode.NARRATIVE_SNIPPET_REVIEW_REQUIRED
    ),
}


def _apply_review(
    slot: TextSlot,
    transform: CaseTransform,
    policy: PublicationPolicy,
    approvals: PublicationApprovals,
) -> None:
    """A field no transform may touch: approve it, withhold it, or block.

    The deterministic summary is the one exception that needs no decision --
    see :func:`~.approvals.is_generated_summary`, which the form generator
    reads from too, so the two cannot drift into disagreeing about which
    fields a reviewer owes a signature on.
    """
    from .substitute import TextSlot

    assert isinstance(slot, TextSlot)  # noqa: S101 - narrows for mypy
    value = slot.read()
    if value is None or (isinstance(value, list) and not value) or value == "":
        return

    if is_generated_summary(slot):
        items = value if isinstance(value, list) else [value]
        slot.write([transform.table.rewrite(item) for item in items])
        return

    approved = approvals.text_for(transform.example_id, slot.path)
    if approved is None:
        code = _REVIEW_CODES.get(
            slot.key, DisclosureCode.CURATOR_TEXT_REVIEW_REQUIRED
        )
        transform.findings.append(
            DisclosureFinding(
                code=code,
                example_id=transform.example_id,
                field_path=slot.path,
                message=(
                    f"{slot.key} is human-authored or verbatim narrative text. "
                    "Automatic redaction is not sufficient: supply approved "
                    "replacement text for this path, or withhold it"
                ),
                detail=(
                    value if isinstance(value, str) else " | ".join(value)
                ),
            )
        )
        return

    if approved.approve_as_written:
        pinned = digest_text(value)
        if pinned != approved.text_sha256:
            transform.findings.append(
                DisclosureFinding(
                    code=DisclosureCode.APPROVAL_DIGEST_MISMATCH,
                    example_id=transform.example_id,
                    field_path=slot.path,
                    message=(
                        f"{slot.key} was approved as written, but the text "
                        "has changed since. Re-read it and update "
                        "text_sha256, or decide again"
                    ),
                    detail=(
                        f"approval pins {approved.text_sha256}; the text now "
                        f"digests to {pinned}"
                    ),
                )
            )
            return
        if isinstance(value, list):
            slot.write([transform.table.rewrite(item) for item in value])
        else:
            slot.write(transform.table.rewrite(value))
        transform.approved_as_written_paths.append(slot.path)
        return

    if approved.withhold:
        slot.write(
            [policy.withheld_text_notice] if slot.is_list else ""
        )
        transform.withheld_paths.append(slot.path)
        return
    slot.write(
        [approved.replacement] if slot.is_list else approved.replacement
    )
    transform.replaced_paths.append(slot.path)


def _scramble_case(
    case: EvidenceCaseVM,
    position: int,
    policy: PublicationPolicy,
    secrets: PublicationSecrets,
    approvals: PublicationApprovals,
) -> CaseTransform:
    override = approvals.country_masking_for(case.example_id)
    transform = CaseTransform(
        example_id=case.example_id,
        pair_id=case.pair_id,
        position=position,
        published_example_id=case_slug(position),
        published_example_label=case_aliases(
            position, policy.pair_alias_template
        ),
        countries_masked=(
            policy.mask_countries if override is None else override
        ),
        sources_masked=policy.mask_source_identifiers,
    )
    book = AliasBook(
        country_template=policy.country_alias_template,
        identifier_template=policy.identifier_alias_template,
        source_template=policy.source_alias_template,
        report_labels=policy.report_alias_labels,
    )
    try:
        book.bind_reports(split_pair_id(case.pair_id))
    except ValueError:
        # A non-canonical pair id still gets aliases; it just cannot seed the
        # two column labels, so its identifiers number from one.
        pass

    date_set = _collect_dates(case, transform)
    report_offset, clinical_offset = _solve_date_offsets(
        case, date_set, policy, secrets, transform
    )
    if report_offset is not None and clinical_offset is not None:
        transform.date_offset_days = report_offset
        transform.clinical_offset_days = clinical_offset
        if report_offset == clinical_offset:
            _register_dates(date_set.dates, report_offset, transform.table)
        else:
            _register_dates(date_set.report_dates, report_offset, transform.table)
            _register_dates(
                date_set.clinical_dates, clinical_offset, transform.table
            )

    for kind in (ValueKind.AGE, ValueKind.HEIGHT, ValueKind.WEIGHT):
        _transform_numeric(case, kind, policy, secrets, transform)
    _transform_countries(
        case, book, mask=transform.countries_masked, transform=transform
    )
    _transform_sources(
        case, book, mask=transform.sources_masked, transform=transform
    )
    _transform_identifiers(case, book, transform)
    # Before the text pass, which rewrites the scope labels: read as the
    # internal page shows them. It makes no difference while the finding is
    # confined to unmasked cases, and it keeps the check independent of
    # whether a later pass happens to have run.
    _note_retained_country_small_count(case, policy, transform)

    transform.findings.extend(
        transform.table.conflict_findings(transform.example_id)
    )

    _apply_cells(case, transform, approvals)
    _apply_text(case, transform, policy, approvals)
    _recompute_separations(case, transform)

    if transform.date_pattern_unchanged:
        transform.findings.append(
            DisclosureFinding(
                code=DisclosureCode.DATE_PATTERN_UNCHANGED,
                example_id=transform.example_id,
                message=(
                    "every date in this case is published as reported: the "
                    "accepted offset moved none of them. Allowed by design — "
                    "a transform that always moved a date would let a reader "
                    "rule out the displayed date — but the published page "
                    "carries this pair's real timeline, so weigh it"
                ),
                detail=(
                    f"offset {transform.date_offset_days:+d} days over "
                    f"{transform.dates_total} date value(s)"
                    if transform.date_offset_days is not None
                    else "no offset was applied"
                ),
            )
        )

    for advisory in transform.advisories:
        transform.findings.append(
            DisclosureFinding(
                code=DisclosureCode.UNASSERTED_RELATION_CHANGED,
                example_id=transform.example_id,
                message=(
                    "a relationship the page does not state changed under the "
                    "accepted offset; confirm no reader depends on it"
                ),
                detail=advisory.detail,
            )
        )

    transform.findings.extend(
        scan_residual(
            case,
            transform.table,
            example_id=transform.example_id,
            published_dates=sorted(transform.table.published_values()),
            policy=policy,
        )
    )
    return transform


# ---------------------------------------------------------------------------
# One page
# ---------------------------------------------------------------------------


def _transform_note(
    policy: PublicationPolicy, transforms: list[CaseTransform]
) -> str:
    """What this run actually did, for the page footer.

    Built from the effective policy rather than written once as prose,
    because the policy is configurable: a footer naming every attribute
    category is wrong for any run that left one of them as reported, and a
    footer that overstates what was done is the one piece of publication
    metadata a reader has no way to check.
    """
    masked_countries = sum(1 for t in transforms if t.countries_masked)
    masked_sources = sum(1 for t in transforms if t.sources_masked)
    total = len(transforms)

    transformed = ["ages", "body measurements", "dates", "report identifiers"]
    retained: list[str] = []
    if masked_sources == total and total:
        transformed.append("shared source identifiers")
    elif masked_sources:
        transformed.append("shared source identifiers in some examples")
    else:
        retained.append("shared source identifiers")
    if masked_countries == total and total:
        transformed.append("countries")
    elif masked_countries:
        transformed.append("countries in some examples")
    else:
        retained.append("countries")

    sentence = f"{_join(transformed).capitalize()} have been transformed"
    if retained:
        sentence += f"; {_join(retained)} are shown as reported"
    split = any(
        t.clinical_offset_days is not None
        and t.clinical_offset_days != t.date_offset_days
        for t in transforms
    )
    note = (
        f"{sentence}. The assessments, scores, contributions and penalties "
        "are those of the original reports."
    )
    if split:
        note += (
            " Clinical dates and report dates were shifted by different "
            "amounts, so the interval between an event and the report "
            "describing it is not the reported interval; intervals within "
            "each of the two are."
        )
    return note


def _join(items: list[str]) -> str:
    """``"a, b and c"`` — a list a reader can read aloud."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def scramble_page(
    page: EvidencePageVM,
    *,
    policy: PublicationPolicy | None = None,
    secrets: PublicationSecrets,
    approvals: PublicationApprovals | None = None,
) -> ScrambleResult:
    """A publication candidate built from an internal page.

    *page* is not modified: the work happens on a deep copy, which
    ``copy.deepcopy`` produces with a single memo, so the view model's
    deliberate object sharing survives into the copy and one rewrite still
    reaches the table, the chip and the finding alike.
    """
    settings = policy or PublicationPolicy()
    decisions = approvals or PublicationApprovals()
    published = page.model_copy(deep=True)
    published.mode = PresentationMode.PUBLICATION

    transforms: list[CaseTransform] = []
    for index, case in enumerate(published.cases, start=1):
        transforms.append(
            _scramble_case(
                case,
                case.position or index,
                settings,
                secrets,
                decisions,
            )
        )

    union = SubstitutionTable()
    for transform in transforms:
        for entry in transform.table.entries.values():
            union.add(
                entry.original, entry.published, kind=entry.kind, note=entry.note
            )

    published.transform_note = _transform_note(settings, transforms)
    findings = [f for transform in transforms for f in transform.findings]
    findings.extend(_scramble_page_level(published, union, settings))

    return ScrambleResult(
        page=published,
        report=partition(
            findings, decisions, advisories_block=settings.advisories_block
        ),
        transforms=transforms,
        key_id=secrets.key_id,
        policy=settings,
    )


def _scramble_page_level(
    page: EvidencePageVM, union: SubstitutionTable, policy: PublicationPolicy
) -> list[DisclosureFinding]:
    """The page's own title, intro, note and provenance.

    Scanned and rewritten with the union of every case's table: a collection's
    introduction may quote a value from any of its examples.
    """
    for slot in walk_text_slots(page):
        if slot.path.startswith("cases["):
            continue
        rule = FREE_TEXT_FIELDS.get(slot.key)
        if rule is FreeTextPolicy.SUBSTITUTE:
            value = slot.read()
            if isinstance(value, str) and value:
                slot.write(union.rewrite(value))
        elif rule is FreeTextPolicy.ALIAS and slot.key == "ProvenanceVM.source":
            slot.write(policy.provenance_source_label if slot.read() else "")

    page_only = EvidencePageVM(
        title=page.title,
        intro=page.intro,
        note=page.note,
        generated_at=page.generated_at,
        provenance=page.provenance,
        mode=page.mode,
    )
    return scan_residual(
        page_only,
        union,
        example_id="",
        published_dates=sorted(union.published_values()),
        policy=policy,
    )


__all__ = [
    "CaseTransform",
    "PresentationMode",
    "ScrambleResult",
    "scramble_page",
]
