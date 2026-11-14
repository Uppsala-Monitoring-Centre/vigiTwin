"""Compute the reference-set characteristics artifact from ``Report`` objects.

The secure side owns the reports; this module owns the arithmetic. A caller
passes a reference set plus either a ``{report_id: Report}`` mapping or anything
satisfying the ``ReportLoader`` protocol it already implements, and gets back an
aggregate-only artifact that carries no report content.

Two counting rules run through everything here:

**Reports are counted once.** A report appearing in two pairs contributes one
row to every report-level table. Tabulating per pair instead would silently
weight such reports double — and reference sets are built from duplicate
*clusters*, so shared reports are the normal case, not an edge case.

**Nothing is dropped for being absent.** Every report lands in exactly one
disposition of every distribution, so the counts partition the denominator and
a thin table is legible as thin rather than looking like a full one.

See: docs/reference_set_characteristics.md
"""

from __future__ import annotations

import statistics
import uuid
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Callable, Iterable, Literal, Mapping, Sequence

from llm_dup_dev.characterization import policy
from llm_dup_dev.characterization.models import (
    CategoricalDistribution,
    CategoryCount,
    CharacterizationReport,
    Concordance,
    DataAvailability,
    NumericSummary,
    PairCharacteristics,
    ReferenceSetCharacteristics,
    ReportCharacteristics,
    ResolutionPolicySummary,
)
from llm_dup_dev.data.protocols import ReportLoader
from llm_dup_dev.feature_generation.matching import (
    compute_matching_aes,
    compute_matching_drugs,
)
from llm_dup_dev.models.report import Report
from llm_dup_dev.run_manifest._git import try_dev_git_commit, try_git_commit
from llm_dup_dev.run_manifest.models import DevPackageInfo
from llm_dup_dev.study_eval.schemas import ReferencePair, ReferenceSet

_PACKAGE_NAME = "llm-dup-dev"

#: Emitted on an empty top-drugs table when no drug in the set carried a
#: countable role, so the emptiness reads as a property of the extract rather
#: than as "these reports mention no drugs".
NO_DRUG_ROLE_REASON = "missing_drug_role"

#: Emitted on an empty HLT table when no adverse event carried a primary MedDRA
#: hierarchy. Deliberately the same string the deterministic AE clinical-picture
#: assessor uses for the same condition.
NO_HIERARCHY_REASON = "missing_hierarchy"

#: Key used in ``reference_label_counts`` for pairs carrying no human label —
#: the normal state of a recall set, where every pair is a positive control.
UNLABELLED_KEY = "unlabelled"

OnMissingReport = Literal["raise", "skip"]


# ---------------------------------------------------------------------------
# Distribution helpers
# ---------------------------------------------------------------------------


def _proportion(count: int, denominator: int) -> float | None:
    """Share of ``denominator``, or ``None`` when there is nothing to divide."""
    if denominator <= 0:
        return None
    return count / denominator


def _rank(counts: Mapping[str, int]) -> list[str]:
    """Order keys by descending count, then key ascending.

    The secondary sort is what makes a top-N list reproducible: ties are common
    in small sets (a dozen substances each seen once), and without it the cut
    would depend on dict insertion order.
    """
    return sorted(counts, key=lambda key: (-counts[key], key))


def _categories(
    counts: Mapping[str, int],
    labels: Mapping[str, str],
    denominator: int,
    *,
    top_n: int | None,
    always: Sequence[str] = (),
) -> tuple[list[CategoryCount], int | None, bool]:
    """Build ranked rows, applying an optional top-N cut.

    Returns ``(rows, truncated_to, boundary_tie)``. ``boundary_tie`` reports
    that the cut fell *inside* a run of equal counts, so a reader knows the
    excluded values were no rarer than the last included one.

    ``always`` names keys to emit even at count zero, so a distribution over a
    closed vocabulary shows the whole vocabulary.
    """
    ranked = _rank(counts)

    truncated_to: int | None = None
    boundary_tie = False
    if top_n is not None and len(ranked) > top_n:
        boundary_tie = counts[ranked[top_n - 1]] == counts[ranked[top_n]]
        ranked = ranked[:top_n]
        truncated_to = top_n

    shown = list(ranked)
    for key in always:
        if key not in counts:
            shown.append(key)

    rows = [
        CategoryCount(
            key=key,
            label=labels.get(key, key),
            count=counts.get(key, 0),
            proportion=_proportion(counts.get(key, 0), denominator),
        )
        for key in shown
    ]
    return rows, truncated_to, boundary_tie


def _resolved_distribution(
    field: str,
    resolutions: Sequence[policy.Resolution],
    *,
    top_n: int | None = None,
    always: Sequence[str] = (),
    keys: Sequence[str | None] | None = None,
    n_out_of_range: int = 0,
) -> CategoricalDistribution:
    """Tabulate one field from per-report :class:`policy.Resolution` outcomes.

    ``keys`` optionally substitutes the tabulated key for each report, so a
    caller can group resolved values differently from how they were resolved —
    age bands are resolved as exact years and then banded. A substituted key of
    ``None`` means "resolved, but not tabulable here"; such reports are counted
    in ``n_out_of_range`` by the caller rather than contributing a category.
    """
    denominator = len(resolutions)
    counts: Counter[str] = Counter()
    labels: dict[str, str] = {}
    dispositions: Counter[str] = Counter()

    for index, resolution in enumerate(resolutions):
        dispositions[resolution.status] += 1
        key = resolution.key if keys is None else keys[index]
        if resolution.resolved and key is not None:
            counts[key] += 1
            labels.setdefault(key, key if keys is not None else (resolution.label or key))

    rows, truncated_to, boundary_tie = _categories(
        counts, labels, denominator, top_n=top_n, always=always
    )
    return CategoricalDistribution(
        field=field,
        denominator=denominator,
        n_resolved=dispositions[policy.RESOLVED] - n_out_of_range,
        n_missing=dispositions[policy.MISSING],
        n_conflicting=dispositions[policy.CONFLICTING],
        n_unrecognised=dispositions[policy.UNRECOGNISED],
        n_out_of_range=n_out_of_range,
        n_distinct=len(counts),
        categories=rows,
        truncated_to=truncated_to,
        boundary_tie=boundary_tie,
    )


def _membership_distribution(
    field: str,
    per_report: Sequence[Mapping[str, str]],
    *,
    top_n: int | None,
    unavailable_reason: str | None = None,
) -> CategoricalDistribution:
    """Tabulate a set-valued field — one report may contribute several keys.

    Counts are **reports containing the term**, not term occurrences, so a
    proportion reads as "share of reports mentioning X". Deduplication happened
    upstream in :mod:`policy`; a report listing the same substance twice is one
    report here.

    A report contributing no key is ``n_missing``: it said nothing about this
    field. There is no conflict disposition — a report naming three drugs is not
    in conflict with itself.
    """
    denominator = len(per_report)
    counts: Counter[str] = Counter()
    labels: dict[str, str] = {}
    n_resolved = 0

    for keyed in per_report:
        if not keyed:
            continue
        n_resolved += 1
        for key, label in keyed.items():
            counts[key] += 1
            labels.setdefault(key, label)

    rows, truncated_to, boundary_tie = _categories(
        counts, labels, denominator, top_n=top_n
    )
    return CategoricalDistribution(
        field=field,
        denominator=denominator,
        n_resolved=n_resolved,
        n_missing=denominator - n_resolved,
        n_conflicting=0,
        n_unrecognised=0,
        n_distinct=len(counts),
        categories=rows,
        truncated_to=truncated_to,
        boundary_tie=boundary_tie,
        unavailable_reason=unavailable_reason if not counts else None,
    )


def _quartiles(values: Sequence[float]) -> tuple[float | None, float | None, float | None]:
    """Return ``(median, q1, q3)`` using the inclusive (type-7) definition.

    ``statistics.quantiles`` needs at least two observations. A single
    observation is degenerate rather than undefined — its median and both
    quartiles are that value — and a reference set legitimately can be that
    small, so returning it beats raising.
    """
    if not values:
        return None, None, None
    if len(values) == 1:
        only = float(values[0])
        return only, only, only
    q1, median, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return float(median), float(q1), float(q3)


def _numeric_summary(
    field: str,
    unit: str,
    values: Sequence[float],
    *,
    n_missing: int = 0,
    n_conflicting: int = 0,
    n_unrecognised_unit: int = 0,
    n_out_of_range: int = 0,
) -> NumericSummary:
    median, q1, q3 = _quartiles(values)
    return NumericSummary(
        field=field,
        unit=unit,
        n_used=len(values),
        n_missing=n_missing,
        n_conflicting=n_conflicting,
        n_unrecognised_unit=n_unrecognised_unit,
        n_out_of_range=n_out_of_range,
        median=median,
        q1=q1,
        q3=q3,
        minimum=float(min(values)) if values else None,
        maximum=float(max(values)) if values else None,
    )


# ---------------------------------------------------------------------------
# Report-level section
# ---------------------------------------------------------------------------


def _plausible_age(resolution: policy.Resolution) -> float | None:
    """The completed-year value behind a resolution, if it is usable at all.

    ``None`` covers both "did not resolve" and "resolved to an implausible
    age". Callers count the two separately; what they share is that the value
    must not reach a median, a band, or a pair-level difference.
    """
    if not resolution.resolved or resolution.key is None:
        return None
    years = float(resolution.key)
    return years if policy.age_is_plausible(years) else None


def _age_sections(
    reports: Sequence[Report],
) -> tuple[NumericSummary, CategoricalDistribution]:
    """Age as a numeric summary and as 10-year bands, sharing one resolution.

    Implausible ages are excluded from *both* — a 200-year-old must neither
    shift the median nor appear as a ``100+`` patient — and are counted so the
    exclusion is visible in each table.
    """
    resolutions = [policy.resolve_age_years(report) for report in reports]

    used: list[float] = []
    band_keys: list[str | None] = []
    n_out_of_range = 0

    for resolution in resolutions:
        years = _plausible_age(resolution)
        if years is None:
            # Distinguish "resolved but implausible" from "never resolved":
            # only the former is an exclusion this table needs to account for.
            if resolution.resolved:
                n_out_of_range += 1
            band_keys.append(None)
            continue
        used.append(years)
        band_keys.append(policy.age_band(years))

    dispositions = Counter(r.status for r in resolutions)
    summary = _numeric_summary(
        "patient_age_years",
        "completed_years",
        used,
        n_missing=dispositions[policy.MISSING],
        n_conflicting=dispositions[policy.CONFLICTING],
        n_unrecognised_unit=dispositions[policy.UNRECOGNISED],
        n_out_of_range=n_out_of_range,
    )
    bands = _resolved_distribution(
        "patient_age_bands",
        resolutions,
        keys=band_keys,
        n_out_of_range=n_out_of_range,
    )
    return summary, bands


def _drug_role_distribution(reports: Sequence[Report]) -> CategoricalDistribution:
    """Roles recorded across drug entries, including the unrecorded ones.

    Counted over *reports*, like every other distribution here: a report is
    counted once per distinct role it records. This is what makes an empty
    top-drugs table readable — it shows at a glance whether the extract simply
    never populated ``role``.
    """
    per_report: list[dict[str, str]] = []
    for report in reports:
        keyed: dict[str, str] = {}
        for drug in report.drugs:
            key = drug.role.value if drug.role is not None else "unrecorded"
            keyed.setdefault(key, key)
        per_report.append(keyed)
    return _membership_distribution("drug_roles", per_report, top_n=None)


def _availability(reports: Sequence[Report]) -> DataAvailability:
    return DataAvailability(
        n_reports_with_narrative=sum(1 for r in reports if r.narrative),
        n_reports_with_any_drug=sum(1 for r in reports if r.drugs),
        n_reports_with_any_suspect_or_interacting_drug=sum(
            1 for r in reports if policy.counted_drugs(r)
        ),
        n_reports_with_any_drug_role_recorded=sum(
            1 for r in reports if policy.has_any_drug_role(r)
        ),
        n_reports_with_any_adverse_event=sum(1 for r in reports if r.adverse_events),
        n_reports_with_any_hlt=sum(1 for r in reports if policy.hlt_keys(r)),
        n_reports_with_case_identifiers=sum(1 for r in reports if r.case_identifiers),
    )


def _report_characteristics(
    reports: Sequence[Report],
    *,
    n_pairs: int,
    n_report_instances: int,
    n_reports_in_multiple_pairs: int,
    n_reports_unavailable: int,
    top_n: int,
) -> ReportCharacteristics:
    availability = _availability(reports)
    age_summary, age_bands = _age_sections(reports)

    any_role_recorded = availability.n_reports_with_any_drug_role_recorded > 0
    any_hlt = availability.n_reports_with_any_hlt > 0

    return ReportCharacteristics(
        n_pairs=n_pairs,
        n_report_instances=n_report_instances,
        n_unique_reports=len(reports),
        n_reports_in_multiple_pairs=n_reports_in_multiple_pairs,
        n_reports_unavailable=n_reports_unavailable,
        availability=availability,
        report_year=_resolved_distribution(
            "report_year", [policy.resolve_report_year(r) for r in reports]
        ),
        country_of_origin=_resolved_distribution(
            "country_of_origin", [policy.resolve_country(r) for r in reports]
        ),
        patient_sex=_resolved_distribution(
            "patient_sex",
            [policy.resolve_sex(r) for r in reports],
            always=("female", "male"),
        ),
        patient_age_years=age_summary,
        patient_age_bands=age_bands,
        drug_roles=_drug_role_distribution(reports),
        top_drugs=_membership_distribution(
            "top_drugs",
            [policy.drug_labels(r) for r in reports],
            top_n=top_n,
            unavailable_reason=None if any_role_recorded else NO_DRUG_ROLE_REASON,
        ),
        top_adverse_events_pt=_membership_distribution(
            "top_adverse_events_pt",
            [policy.pt_keys(r) for r in reports],
            top_n=top_n,
        ),
        top_adverse_events_hlt=_membership_distribution(
            "top_adverse_events_hlt",
            [policy.hlt_keys(r) for r in reports],
            top_n=top_n,
            unavailable_reason=None if any_hlt else NO_HIERARCHY_REASON,
        ),
    )


# ---------------------------------------------------------------------------
# Pair-level section
# ---------------------------------------------------------------------------


def _concordance(
    field: str,
    pairs: Sequence[tuple[Report, Report]],
    resolve: Callable[[Report], policy.Resolution],
    *,
    n_pairs: int,
) -> Concordance:
    """Agreement on one field across the two reports of each pair.

    Pairs where either side has no usable value are ``not_comparable``, never
    ``different``: two reports that are both silent about country have not
    disagreed about it, and folding them together would make the "different
    country" figure a function of data coverage.
    """
    n_same = n_different = 0
    for left, right in pairs:
        first, second = resolve(left), resolve(right)
        if not (first.resolved and second.resolved):
            continue
        if first.key == second.key:
            n_same += 1
        else:
            n_different += 1

    comparable = n_same + n_different
    return Concordance(
        field=field,
        n_pairs=n_pairs,
        n_same=n_same,
        n_different=n_different,
        n_not_comparable=n_pairs - comparable,
        proportion_same_comparable=_proportion(n_same, comparable),
        proportion_same_all=_proportion(n_same, n_pairs),
    )


def _absolute_difference(
    field: str,
    unit: str,
    pairs: Sequence[tuple[Report, Report]],
    resolve: Callable[[Report], policy.Resolution],
    *,
    n_pairs: int,
    value_of: Callable[[policy.Resolution], float | None],
) -> tuple[NumericSummary, int]:
    """``|Δ|`` over the pairs where both sides yielded a usable number.

    ``value_of`` extracts the number, returning ``None`` where the resolution
    cannot supply one — which is how an implausible age is kept out of a
    difference just as it is kept out of a median.

    Returns the summary and how many of the differences were exactly zero.
    """
    differences: list[float] = []
    n_conflicting = 0
    n_identical = 0

    for left, right in pairs:
        first, second = resolve(left), resolve(right)
        if first.status == policy.CONFLICTING or second.status == policy.CONFLICTING:
            n_conflicting += 1
            continue
        first_value, second_value = value_of(first), value_of(second)
        if first_value is None or second_value is None:
            continue
        difference = abs(first_value - second_value)
        differences.append(difference)
        if difference == 0:
            n_identical += 1

    summary = _numeric_summary(
        field,
        unit,
        differences,
        n_missing=n_pairs - len(differences) - n_conflicting,
        n_conflicting=n_conflicting,
    )
    return summary, n_identical


def _numeric_key(resolution: policy.Resolution) -> float | None:
    """The resolution's key read as a number, or ``None`` if it did not resolve."""
    if not resolution.resolved or resolution.key is None:
        return None
    return float(resolution.key)


def _pair_characteristics(
    pairs: Sequence[tuple[Report, Report]],
    *,
    n_pairs: int,
) -> PairCharacteristics:
    evaluable = len(pairs)

    year_difference, _ = _absolute_difference(
        "year_difference",
        "years",
        pairs,
        policy.resolve_report_year,
        n_pairs=evaluable,
        value_of=_numeric_key,
    )
    age_difference, n_identical_age = _absolute_difference(
        "age_difference_years",
        "completed_years",
        pairs,
        policy.resolve_age_years,
        n_pairs=evaluable,
        value_of=_plausible_age,
    )

    # Reuse the pipeline's own set-overlap logic rather than re-deriving it, so
    # "shared drugs" here means exactly what it means to the adjudicator. The
    # narrow helpers are used deliberately: ``compute_matches`` would also run
    # demographic and clinical-date matching that this artifact never reads.
    shared_drug_counts = [
        float(len(compute_matching_drugs(left, right)[0])) for left, right in pairs
    ]
    shared_ae_counts = [
        float(len(compute_matching_aes(left, right)[0])) for left, right in pairs
    ]

    return PairCharacteristics(
        n_pairs=n_pairs,
        n_pairs_evaluable=evaluable,
        country=_concordance("country", pairs, policy.resolve_country, n_pairs=evaluable),
        report_year=_concordance(
            "report_year", pairs, policy.resolve_report_year, n_pairs=evaluable
        ),
        patient_sex=_concordance(
            "patient_sex", pairs, policy.resolve_sex, n_pairs=evaluable
        ),
        year_difference=year_difference,
        age_difference_years=age_difference,
        n_pairs_with_identical_age=n_identical_age,
        shared_drugs=_numeric_summary("shared_drugs", "drugs", shared_drug_counts),
        shared_adverse_events_pt=_numeric_summary(
            "shared_adverse_events_pt", "preferred_terms", shared_ae_counts
        ),
        n_pairs_with_any_shared_drug=sum(1 for n in shared_drug_counts if n > 0),
        n_pairs_with_any_shared_adverse_event=sum(1 for n in shared_ae_counts if n > 0),
    )


# ---------------------------------------------------------------------------
# The reports seam
# ---------------------------------------------------------------------------


def _referenced_report_ids(reference_sets: Iterable[ReferenceSet]) -> list[str]:
    """Every report id the supplied sets name, once, in first-appearance order."""
    seen: dict[str, None] = {}
    for reference_set in reference_sets:
        for pair in reference_set.pairs:
            seen.setdefault(pair.report_id1, None)
            seen.setdefault(pair.report_id2, None)
    return list(seen)


def _resolve_reports(
    reference_sets: Sequence[ReferenceSet],
    reports: Mapping[str, Report] | ReportLoader,
    on_missing_report: OnMissingReport,
) -> dict[str, Report]:
    """Fetch each referenced report exactly once, whichever seam was supplied.

    A ``ReportLoader`` is detected structurally — the protocol is
    ``runtime_checkable`` and declares only methods, so a ``Mapping`` can never
    satisfy it. This is the same runtime protocol check ``PipelineRunner``
    applies to ``PairContextSink``.

    Missing ids are reported **all at once**, in the style of
    ``study_eval.preflight``: discovering them one per run wastes a secure-side
    round trip each time.
    """
    if on_missing_report not in ("raise", "skip"):
        raise ValueError(
            f"on_missing_report must be 'raise' or 'skip', got "
            f"{on_missing_report!r}."
        )

    wanted = _referenced_report_ids(reference_sets)
    resolved: dict[str, Report] = {}
    missing: list[str] = []

    is_loader = not isinstance(reports, Mapping) and isinstance(reports, ReportLoader)
    for report_id in wanted:
        if is_loader:
            try:
                resolved[report_id] = reports.load_report(report_id)  # type: ignore[union-attr]
            except KeyError:
                missing.append(report_id)
        else:
            report = reports.get(report_id)  # type: ignore[union-attr]
            if report is None:
                missing.append(report_id)
            else:
                resolved[report_id] = report

    if missing and on_missing_report == "raise":
        shown = missing[:5]
        raise KeyError(
            f"no report supplied for {len(missing)} referenced report id(s): "
            f"{shown}{'...' if len(missing) > 5 else ''}. Pass "
            f"on_missing_report='skip' to characterise the rest and record the "
            f"gap instead."
        )
    return resolved


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolution_policy_summary(top_n: int) -> ResolutionPolicySummary:
    """The policy record embedded in every artifact this module builds."""
    return ResolutionPolicySummary(
        source_preference=[policy.STRUCTURED_SOURCE],
        conflict_handling=policy.CONFLICT_HANDLING,
        age_unit_conversions=dict(policy.AGE_UNIT_TO_YEARS),
        age_band_width_years=policy.AGE_BAND_WIDTH_YEARS,
        age_plausible_range=list(policy.AGE_PLAUSIBLE_RANGE),
        country_key_normalisation=policy.COUNTRY_KEY_NORMALISATION,
        drug_roles_counted=[role.value for role in policy.COUNTED_DRUG_ROLES],
        top_n=top_n,
        tie_break=policy.TIE_BREAK,
    )


def _label_counts(pairs: Sequence[ReferencePair]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for pair in pairs:
        counts[pair.reference_label or UNLABELLED_KEY] += 1
    return dict(sorted(counts.items()))


def _characterise(
    reference_set: ReferenceSet,
    by_id: Mapping[str, Report],
    *,
    top_n: int,
) -> ReferenceSetCharacteristics:
    """Build one set's block from already-fetched reports."""
    instance_counts: Counter[str] = Counter()
    order: dict[str, None] = {}
    for pair in reference_set.pairs:
        for report_id in (pair.report_id1, pair.report_id2):
            instance_counts[report_id] += 1
            order.setdefault(report_id, None)

    unique_reports = [by_id[rid] for rid in order if rid in by_id]
    unavailable = [rid for rid in order if rid not in by_id]

    evaluable_pairs = [
        (by_id[pair.report_id1], by_id[pair.report_id2])
        for pair in reference_set.pairs
        if pair.report_id1 in by_id and pair.report_id2 in by_id
    ]

    return ReferenceSetCharacteristics(
        set_id=reference_set.set_id,
        role=reference_set.role,
        label=reference_set.label,
        provenance=reference_set.provenance,
        screening_mode=reference_set.screening_mode,
        reference_label_counts=_label_counts(reference_set.pairs),
        reports=_report_characteristics(
            unique_reports,
            n_pairs=len(reference_set.pairs),
            n_report_instances=2 * len(reference_set.pairs),
            n_reports_in_multiple_pairs=sum(
                1 for rid in order if instance_counts[rid] > 1 and rid in by_id
            ),
            n_reports_unavailable=len(unavailable),
            top_n=top_n,
        ),
        pairs=_pair_characteristics(
            evaluable_pairs, n_pairs=len(reference_set.pairs)
        ),
    )


def build_reference_set_characteristics(
    reference_set: ReferenceSet,
    reports: Mapping[str, Report] | ReportLoader,
    *,
    top_n: int = 10,
    on_missing_report: OnMissingReport = "raise",
) -> ReferenceSetCharacteristics:
    """Describe one reference set's reports and pairs.

    ``reports`` is either a ``{report_id: Report}`` mapping or a ``ReportLoader``;
    each referenced report is fetched once regardless of how many pairs name it.

    ``on_missing_report='raise'`` (the default) refuses to describe a set whose
    reports are not all available, because a silently smaller denominator is the
    kind of error a Table 1 carries into a paper. ``'skip'` characterises what
    is available and records the gap in ``n_reports_unavailable``.
    """
    by_id = _resolve_reports([reference_set], reports, on_missing_report)
    return _characterise(reference_set, by_id, top_n=top_n)


def build_characterization_report(
    reference_sets: Sequence[ReferenceSet],
    reports: Mapping[str, Report] | ReportLoader,
    *,
    top_n: int = 10,
    on_missing_report: OnMissingReport = "raise",
    dataset_id: str | None = None,
    notes: str | None = None,
    characterization_id: str | None = None,
    created_at: datetime | None = None,
    dev_repo_path: str | Path | None = None,
) -> CharacterizationReport:
    """Describe every supplied reference set, one block per set.

    Reports shared between sets are fetched once for the whole run, so passing
    all of a study's sets together costs the secure side one lookup per distinct
    report rather than one per set.

    ``notes`` is worth filling in: which ``Report`` objects were passed changes
    the numbers — reports read straight from the database and reports that have
    been through ``merge_narrative_demographics`` resolve differently — and
    nothing in the artifact can detect which it was.

    Auto-populated when not provided, mirroring ``build_dataset_manifest``:
    ``created_at``, ``characterization_id`` (12-char hex), ``dev_package.version``
    from installed metadata, and ``dev_git_commit`` from git.
    """
    if not reference_sets:
        raise ValueError(
            "at least one reference set is required; a run that described "
            "nothing should not look like a run that succeeded."
        )

    by_id = _resolve_reports(list(reference_sets), reports, on_missing_report)

    return CharacterizationReport(
        created_at=created_at or datetime.now(timezone.utc),
        characterization_id=characterization_id or uuid.uuid4().hex[:12],
        dataset_id=dataset_id,
        dev_package=DevPackageInfo(
            name=_PACKAGE_NAME, version=_detect_package_version()
        ),
        dev_git_commit=(
            try_git_commit(dev_repo_path)
            if dev_repo_path is not None
            else try_dev_git_commit()
        ),
        policy=resolution_policy_summary(top_n),
        sets=[
            _characterise(reference_set, by_id, top_n=top_n)
            for reference_set in reference_sets
        ],
        notes=notes,
    )


def _detect_package_version() -> str | None:
    try:
        return version(_PACKAGE_NAME)
    except PackageNotFoundError:
        return None


__all__ = [
    "NO_DRUG_ROLE_REASON",
    "NO_HIERARCHY_REASON",
    "UNLABELLED_KEY",
    "build_reference_set_characteristics",
    "build_characterization_report",
    "resolution_policy_summary",
]
