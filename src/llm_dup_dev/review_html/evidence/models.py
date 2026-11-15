"""View models for the evidence review format.

These models exist to keep two things apart that the detailed review format
deliberately conflates:

* **display** — the text a reader sees, and
* **attribution** — why vigiTwin's assessment touched that element.

In the detailed format a cell's colour *is* a comparison of its two displayed
values. Here a colour is only ever a consequence of
:class:`Attribution` records joined from the stored assessment, and
:attr:`EvidenceCell.text` is the single field holding a report value. A later
publication phase can therefore rewrite every ``text`` — shifting dates,
offsetting ages, masking countries — without touching a single
:class:`EvidenceRole`, because no attribution carries a source value to go
stale. That property is enforced by a test which rewrites every ``text`` and
asserts the rest of the tree is byte-identical.

Attributions record *why*: a feature name, a final severity, a contribution
in decades. None of that is patient-identifying, so it survives the
publication transform unchanged and keeps the colouring faithful to the
original assessment.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Colour semantics
# ---------------------------------------------------------------------------


class EvidenceRole(str, Enum):
    """What the assessment says about an element — the only colour driver.

    ``ANCHOR`` and ``MATCHING`` are both green: the first is a member of the
    pattern that *defined* the anchor population, the second contributed
    additional matching evidence. They are kept apart because the distinction
    is real and the summary states it — an anchor-defining set does not also
    contribute a term to the matching score — even though they render alike.

    ``NEUTRAL`` covers everything the assessment did not act on: unscored
    agreements, differences it did not treat as inconsistencies, missing
    values, withheld values, and suppressed findings.
    """

    ANCHOR = "anchor"
    MATCHING = "matching"
    INCONSISTENT = "inconsistent"
    NEUTRAL = "neutral"

    @property
    def css_class(self) -> str:
        """The stylesheet class for this role."""
        if self is EvidenceRole.INCONSISTENT:
            return "value-mismatch"
        if self is EvidenceRole.NEUTRAL:
            return "value-neutral"
        return "value-match"

    @property
    def is_positive(self) -> bool:
        """True for the two green roles."""
        return self in (EvidenceRole.ANCHOR, EvidenceRole.MATCHING)


#: Role precedence when several attributions land on one element. Red wins
#: over green: a retained inconsistency is a claim the assessment made
#: *against* that value, whereas matching evidence is additive. Both
#: attributions stay on the cell, so a tooltip can still show the match.
_ROLE_PRECEDENCE: dict[EvidenceRole, int] = {
    EvidenceRole.NEUTRAL: 0,
    EvidenceRole.MATCHING: 1,
    EvidenceRole.ANCHOR: 1,
    EvidenceRole.INCONSISTENT: 2,
}


def stronger_role(current: EvidenceRole, candidate: EvidenceRole) -> EvidenceRole:
    """Return whichever role takes precedence, keeping *current* on a tie."""
    if _ROLE_PRECEDENCE[candidate] > _ROLE_PRECEDENCE[current]:
        return candidate
    return current


class ValueKind(str, Enum):
    """What kind of report value a cell holds.

    Not used for colouring. It exists so the later publication layer can
    dispatch its transforms — shift a date, offset an age, mask a country —
    without re-deriving from context what each cell contains.
    """

    SEX = "sex"
    AGE = "age"
    HEIGHT = "height"
    WEIGHT = "weight"
    COUNTRY = "country"
    REPORT_DATE = "report_date"
    DRUG_SUBSTANCE = "drug_substance"
    DRUG_ROLE = "drug_role"
    DRUG_FORM = "drug_form"
    DRUG_STRENGTH = "drug_strength"
    DRUG_START_DATE = "drug_start_date"
    DRUG_END_DATE = "drug_end_date"
    AE_TERM = "ae_term"
    AE_ONSET_DATE = "ae_onset_date"
    IDENTIFIER = "identifier"
    COUNT = "count"
    SCORE = "score"
    FREE_TEXT = "free_text"


class AttributionKind(str, Enum):
    """Why the assessment touched an element."""

    ANCHOR_MEMBER = "anchor_member"
    SHARED_SET_MEMBER = "shared_set_member"
    MATCHING_FEATURE = "matching_feature"
    RETAINED_INCONSISTENCY = "retained_inconsistency"
    DOWNGRADED_RETAINED = "downgraded_retained"
    SUPPRESSED_FINDING = "suppressed_finding"


class EvidenceSource(str, Enum):
    """Which stored artifact field an attribution was read from."""

    MATCHING_SET = "matching_set"
    ANCHOR = "anchor"
    FEATURE_CONTRIBUTION = "feature_contribution"
    INCONSISTENCY = "inconsistency"
    CLINICAL_DATE_FINDING = "clinical_date_finding"
    EXPLICIT_LINK = "explicit_link"
    SOURCE_CASE = "source_case"


class Attribution(BaseModel):
    """One reason the assessment touched an element.

    Deliberately carries no report value: ``feature`` names a field of the
    assessment, not a patient datum. That is what lets a publication
    transform rewrite displayed values while leaving attribution intact.
    """

    kind: AttributionKind
    source: EvidenceSource
    #: The assessment's own feature name, e.g. ``"drug_set"``,
    #: ``"patient_sex"``, ``"clinical_date"``. Kept verbatim so a reviewer can
    #: trace a colour back to the artifact field that caused it.
    feature: str
    #: The severity actually counted — for a downgraded clinical-date finding
    #: this is the *final* severity, not the one before the downgrade.
    severity: str | None = None
    #: Evidence in decades (``-log10(p_f)``), where the source provides one.
    contribution: float | None = None
    #: A short reader-facing phrase for a tooltip. Never parsed by anything.
    note: str = ""


class EvidenceCell(BaseModel):
    """One displayed value plus the assessment's verdict on it."""

    #: The only field holding a report value, and so the only field a
    #: publication transform rewrites.
    text: str
    kind: ValueKind
    role: EvidenceRole = EvidenceRole.NEUTRAL
    attributions: list[Attribution] = Field(default_factory=list)
    #: The value was not reported. Renders as an em dash and is always
    #: neutral — an absence is not evidence.
    missing: bool = False
    #: The value exists but is deliberately not shown. Distinct from
    #: ``missing``: a reader must be able to tell "not reported" from "not
    #: shown here". Set by the later publication phase, never in this one.
    withheld: bool = False

    @property
    def css_class(self) -> str:
        """Stylesheet class, forced neutral when there is nothing to show."""
        if self.missing:
            return "value-neutral"
        return self.role.css_class

    @property
    def title(self) -> str:
        """Tooltip text: the attribution notes, in order, semicolon-joined."""
        return "; ".join(a.note for a in self.attributions if a.note)


def missing_cell(kind: ValueKind, text: str = "") -> EvidenceCell:
    """A cell for a value a report did not supply.

    Rendered blank. An unpopulated field in an adverse event report is absence
    of evidence, not evidence of absence: nobody recorded the patient's age,
    which is not the same as establishing that there is no age to record. Words
    like "Not reported", or a dash standing in for them, read as an assertion
    the data does not support, and they crowd a column a reader is scanning.

    The display text carries no information either way. ``missing`` is the
    structured fact, so a consumer — including the later publication phase,
    whose "Withheld" is a genuinely different state — reads that rather than
    the text.
    """
    return EvidenceCell(text=text, kind=kind, missing=True)


# ---------------------------------------------------------------------------
# Report comparison rows
# ---------------------------------------------------------------------------


class PresenceVM(BaseModel):
    """Which reports carry an entity: ``Both`` / ``A only`` / ``B only``."""

    label: str
    role: EvidenceRole = EvidenceRole.NEUTRAL

    @property
    def css_class(self) -> str:
        return self.role.css_class


class DrugEntryVM(BaseModel):
    """One drug record as reported — one row of a report's entry list.

    Every instance is kept rather than collapsed, because repeated records of
    the same substance are usually distinct treatment periods, and the
    assessment can attribute a matching date to one period and not another.
    """

    drug_role: EvidenceCell | None = None
    form: EvidenceCell | None = None
    strength: EvidenceCell | None = None
    start_date: EvidenceCell | None = None
    end_date: EvidenceCell | None = None

    @property
    def cells(self) -> list[EvidenceCell]:
        """The populated cells, in display order."""
        ordered = [
            self.drug_role,
            self.form,
            self.strength,
            self.start_date,
            self.end_date,
        ]
        return [c for c in ordered if c is not None]


class DrugEvidenceRow(BaseModel):
    """One substance (or combination), as each report records it.

    The substance is named inside each report's own column rather than in a
    shared label column, using that report's own spelling. Both name cells
    carry the same attributions — the assessment matched the substance, not
    one report's rendering of it — so a publication transform can rewrite
    either without disturbing the colour.
    """

    #: The canonical name cell: the attribution carrier and the sort identity.
    #: The two side cells mirror its role and attributions.
    name: EvidenceCell
    #: ``None`` when that report does not carry the substance at all.
    left_name: EvidenceCell | None = None
    right_name: EvidenceCell | None = None
    left_entries: list[DrugEntryVM] = Field(default_factory=list)
    right_entries: list[DrugEntryVM] = Field(default_factory=list)
    presence: PresenceVM
    row_role: EvidenceRole = EvidenceRole.NEUTRAL
    sort_key: str = ""
    #: 0 both reports, 1 report A only, 2 report B only.
    presence_rank: int = 0
    #: 0 when a report carries this substance in a key role (suspected or
    #: interacting), 1 otherwise. A drug only one report lists matters more
    #: when that report called it suspected than when it was concomitant.
    key_rank: int = 1

    @property
    def row_css_class(self) -> str:
        if self.row_role is EvidenceRole.INCONSISTENT:
            return "row-mismatch"
        if self.row_role.is_positive:
            return "row-match"
        return "row-neutral"


class AEEvidenceRow(BaseModel):
    """One MedDRA preferred term, as each report records it.

    Written into each report's own column rather than a shared label column,
    so a reader can see at a glance what each report actually listed.
    """

    #: The canonical term cell: the attribution carrier and the sort identity.
    term: EvidenceCell
    #: ``None`` when that report does not carry the term.
    left_term: EvidenceCell | None = None
    right_term: EvidenceCell | None = None
    presence: PresenceVM
    #: 0 both reports, 1 report A only, 2 report B only.
    presence_rank: int = 0
    #: Event-level onset dates, kept so the compact two-column table does not
    #: discard them. Rendered in a detail block, not in the main table.
    left_onsets: list[EvidenceCell] = Field(default_factory=list)
    right_onsets: list[EvidenceCell] = Field(default_factory=list)
    row_role: EvidenceRole = EvidenceRole.NEUTRAL
    sort_key: str = ""

    @property
    def has_onsets(self) -> bool:
        return bool(self.left_onsets or self.right_onsets)

    @property
    def row_css_class(self) -> str:
        if self.row_role is EvidenceRole.INCONSISTENT:
            return "ae-mismatch"
        if self.row_role.is_positive:
            return "ae-match"
        return "ae-neutral"


class CharacteristicRow(BaseModel):
    """One demographic or report-metadata field, side by side."""

    label: str
    left: EvidenceCell
    right: EvidenceCell
    #: Verbatim narrative snippets behind a narrative-derived value, per side.
    left_evidence: list[str] = Field(default_factory=list)
    right_evidence: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Context and matching evidence
# ---------------------------------------------------------------------------


class SharedSetVM(BaseModel):
    """The members of a shared drug or adverse-event set, named explicitly."""

    label: str
    members: list[EvidenceCell] = Field(default_factory=list)
    #: True when this set defined the anchor population.
    is_anchor: bool = False

    @property
    def count(self) -> int:
        return len(self.members)


class FreqRowVM(BaseModel):
    """Co-occurrence counts for one reporting scope.

    The counts are for co-occurrence of *every* term in the respective set,
    not for the individual terms, and they include the two reports under
    review. Values are pre-formatted strings so grouping is decided here
    rather than in the template.
    """

    scope_label: str
    n_drugs: str
    n_aes: str
    n_both: str


class MatchingFeatureRow(BaseModel):
    """One additional matching feature and its contribution, in decades."""

    label: EvidenceCell
    contribution: float
    contribution_text: str


class ReconciliationVM(BaseModel):
    """How the individual contributions relate to the stored matching score.

    These do not simply add up, and the difference is not a rounding error.
    The stored score is ``max(0, -log10(P_match + epsilon))`` where
    ``P_match = 1 - (1 - p_joint)^M``, and the two numbers shown — the
    contributions total ``-log10(p_joint)`` and the anchor adjustment
    ``-log10(M)`` — add to ``-log10(p_joint x M)``, which tracks that
    expression only while ``p_joint x M`` is small. The score is therefore
    **bounded at both ends**:

    * at the top by ``epsilon``, so a chance-match probability far below it
      yields ``-log10(epsilon)`` however much evidence the contributions imply;
    * at the bottom by zero, reached as ``p_joint x M`` approaches one — where
      a chance match is no longer unlikely at all, ``P_match`` saturates
      towards one and the additive figure, which can go negative, stops
      describing anything.

    The page shows ``additive_total`` as the contributions table's total and
    says nothing about the bounds, which move no classification. The flags
    remain because they decide whether a difference between that total and
    the stored score is a bound or a disagreement worth a gap.
    """

    contributions_total: float
    contributions_total_text: str
    anchor_adjustment: float | None = None
    anchor_adjustment_text: str = ""
    #: ``contributions_total + anchor_adjustment``: what the two shown numbers
    #: come to, which is the figure a reader adds up. Not the stored score.
    additive_total: float = 0.0
    additive_total_text: str = ""
    stored_score: float
    stored_score_text: str
    #: ``-log10(epsilon)`` when the run's epsilon is known, else ``None``.
    score_ceiling: float | None = None
    ceiling_reached: bool = False
    #: The score sits at its lower bound of zero while the additive figure is
    #: negative — the floor, the mirror of ``ceiling_reached``.
    floor_reached: bool = False


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------


class DiscrepancyVM(BaseModel):
    """One inconsistency finding, as the assessment recorded it."""

    #: What the finding is about, in the reader's terms — "Drugs", "Adverse
    #: events", "Age". Its own column, so the findings can be scanned by kind
    #: without reading each sentence.
    feature_label: str = ""
    #: The severity that was counted. For a downgraded finding this is the
    #: final severity; for a suppressed one it is the severity the row still
    #: carries, which contributed nothing.
    severity: str
    text: str
    #: False only for a suppressed finding, which stayed in the record but
    #: contributed nothing to the penalty.
    retained: bool = True
    suppressed: bool = False
    suppressed_by: str = ""
    #: Non-empty when a clinical-date finding was downgraded; each entry is
    #: the producing module's own reason string.
    downgrades: list[str] = Field(default_factory=list)
    #: What this row added to the inconsistency penalty: the weight the run
    #: applied to its severity, or ``0.0`` for a suppressed row, which
    #: contributed nothing. ``None`` when the run's weights are not known —
    #: no classification was saved, or its penalty does not carry this
    #: severity — and the page then shows the severity alone rather than
    #: quoting a default weight the run may not have used.
    weight: float | None = None
    #: ``weight`` rendered, e.g. ``"+1.0"``; empty when it is unknown.
    weight_text: str = ""

    @property
    def row_css_class(self) -> str:
        """Row tint: a retained finding is red, a superseded one is not.

        The same two classes the drug and adverse-event tables use, so one
        visual language covers the whole page. A superseded finding stays
        untinted because it contributed nothing — tinting it red would say
        the assessment weighed it.
        """
        return "row-mismatch" if self.retained else "row-neutral"


class AssessmentRowVM(BaseModel):
    """One row of the vigiTwin assessment table."""

    label: str
    text: str


class TileState(str, Enum):
    """Why an outcome tile shows what it shows.

    Four states, because a reader must be able to tell a verdict from its
    absence, and two different absences from each other: nobody has entered
    one yet, versus this pair was deliberately not assessed.
    """

    VALUE = "value"
    NOT_ENTERED = "not_entered"
    NOT_ASSESSED = "not_assessed"
    INCONCLUSIVE = "inconclusive"


class OutcomeTileVM(BaseModel):
    """One header tile: a classification, or the stated absence of one."""

    label: str
    value: str
    state: TileState = TileState.VALUE

    @property
    def css_class(self) -> str:
        return "tile-value" if self.state is TileState.VALUE else "tile-absent"


class ReferenceVerdictVM(BaseModel):
    """The reference label, and whether vigiTwin agreed with it.

    Agreement is computed in the *binary* space the reference label lives in
    (``duplicate`` / ``non_duplicate``), under a named positive-class rule —
    never by string-comparing a reference label against a pipeline label,
    which reports a disagreement whenever the two vocabularies differ.
    """

    reference_label: str
    display: str
    set_label: str | None = None
    #: ``None`` when no comparison is defined — either side abstaining, or no
    #: vigiTwin label available.
    agrees_with_vigitwin: bool | None = None
    positive_class_name: str = "primary"
    comparison_note: str = ""


class EvidenceTextVM(BaseModel):
    """A prose block: either the deterministic summary or a human's reading."""

    heading: str
    paragraphs: list[str] = Field(default_factory=list)
    #: True for human-authored text, which is never generated or overwritten.
    is_human: bool = False


# ---------------------------------------------------------------------------
# Gaps
# ---------------------------------------------------------------------------


class EvidenceGapCode(str, Enum):
    """Something the stored artifacts could not support.

    A gap is rendered as a footnote rather than silently dropped. An evidence
    review that quietly omits what it could not compute is indistinguishable
    from one where there was nothing to show.
    """

    DRUG_SET_TERMS_MISSING = "drug_set_terms_missing"
    DRUG_SET_TERMS_RECOVERED = "drug_set_terms_recovered"
    CLINICAL_DATE_VALUE_MISSING = "clinical_date_value_missing"
    CLINICAL_DATE_VALUE_RECOVERED = "clinical_date_value_recovered"
    SCREENING_VERDICT_UNREADABLE = "screening_verdict_unreadable"
    UNMAPPED_INCONSISTENCY = "unmapped_inconsistency"
    UNKNOWN_ENTITY_LABEL = "unknown_entity_label"
    RUN_SETTINGS_MISSING = "run_settings_missing"
    FREQUENCY_STATS_MISSING = "frequency_stats_missing"
    NO_CLASSIFICATION = "no_classification"
    NO_FEATURES = "no_features"
    SCORE_UNRECONCILED = "score_unreconciled"


#: Gaps that explain *how* something shown was obtained, rather than what is
#: missing. They are recorded like any other gap — a test can assert them, and
#: a caller can read them off the case — but the page does not print them: a
#: reader-facing "Limitations" list earns its space by naming what is absent,
#: and a provenance note names something that is right there in the tables.
_PROVENANCE_CODES = frozenset(
    {
        EvidenceGapCode.DRUG_SET_TERMS_RECOVERED,
        EvidenceGapCode.CLINICAL_DATE_VALUE_RECOVERED,
    }
)


class EvidenceGap(BaseModel):
    """One recorded gap, with the reader-facing sentence that explains it."""

    code: EvidenceGapCode
    message: str

    @property
    def is_provenance(self) -> bool:
        """Whether this records how evidence was obtained, not what is absent.

        The page's limitations list is filtered on this, so recovering a date
        from a description stays in the record without costing the reader a
        paragraph about something the tables already show.
        """
        return self.code in _PROVENANCE_CODES


# ---------------------------------------------------------------------------
# Case and page
# ---------------------------------------------------------------------------


class EvidenceCaseVM(BaseModel):
    """One report pair, as one section of an evidence review.

    This is the unit a later phase concatenates: a supplement holding a dozen
    examples is the same template over a longer ``EvidencePageVM.cases``.
    """

    pair_id: str
    example_id: str
    #: What a reader cites this case as, and what the heading shows: the
    #: curator's ``example_id`` where a manifest gave one, otherwise a
    #: readable positional form. Distinct from ``example_id``, which is a
    #: slug — the filename stem and the section anchor — and may not be
    #: presentable. Empty where nothing supplied a framing, in which case the
    #: heading shows no label rather than an internal identifier.
    example_label: str = ""
    #: Where this case sits in the collection it belongs to, and how many
    #: cases that collection holds. Carried on the case rather than taken
    #: from the template's loop position, because the batch path writes each
    #: case into its own file: a standalone ``S3.html`` must still say
    #: "3 of 12" and not "1 of 1". ``None`` where the case stands alone.
    position: int | None = None
    position_total: int | None = None
    kicker: str = ""
    title: str = ""
    lede: str = ""

    outcomes: list[OutcomeTileVM] = Field(default_factory=list)
    reference_verdict: ReferenceVerdictVM | None = None

    #: The human reading, when one was supplied, and/or the deterministic
    #: summary. Rendered in list order.
    narrative_blocks: list[EvidenceTextVM] = Field(default_factory=list)

    characteristics: list[CharacteristicRow] = Field(default_factory=list)
    context_note: str = ""
    drugs: list[DrugEvidenceRow] = Field(default_factory=list)
    adverse_events: list[AEEvidenceRow] = Field(default_factory=list)

    #: Whether the inconsistency penalty was actually compared against a
    #: same-case tolerance. ``False`` when the question was never reached —
    #: independence was not rejected, or the relationship was pinned on source
    #: context — in which case the findings carry weights but none of those
    #: weights bore on the classification, and section 3 says so.
    findings_weighed: bool = False

    shared_sets: list[SharedSetVM] = Field(default_factory=list)
    anchor_note: str = ""
    freq_rows: list[FreqRowVM] = Field(default_factory=list)
    matching_features: list[MatchingFeatureRow] = Field(default_factory=list)
    reconciliation: ReconciliationVM | None = None

    explicit_link_note: str = ""
    source_case_note: str = ""
    narrative_note: str = ""

    assessment_rows: list[AssessmentRowVM] = Field(default_factory=list)
    discrepancies: list[DiscrepancyVM] = Field(default_factory=list)

    gaps: list[EvidenceGap] = Field(default_factory=list)

    @property
    def position_text(self) -> str:
        """``"3 of 12"``, or empty where there is no collection to place.

        A lone case is not "1 of 1": the phrase only informs a reader who has
        more cases to get through.
        """
        if self.position is None or not self.position_total:
            return ""
        if self.position_total < 2:
            return ""
        return f"{self.position} of {self.position_total}"

    @property
    def retained_discrepancies(self) -> list[DiscrepancyVM]:
        return [d for d in self.discrepancies if d.retained]

    @property
    def suppressed_discrepancies(self) -> list[DiscrepancyVM]:
        return [d for d in self.discrepancies if not d.retained]


@lru_cache(maxsize=1)
def _installed_version() -> str | None:
    """The installed distribution's version, or ``None`` when not installed.

    Read from installed metadata, the same source
    ``run_manifest.build_run_manifest`` records as ``dev_package.version``, so
    a page and a manifest written by one run cannot disagree about which
    version produced them.
    """
    try:
        return distribution_version("llm-dup-dev")
    except PackageNotFoundError:  # pragma: no cover - source checkout only
        return None


class ProvenanceVM(BaseModel):
    """What produced this review, for the footer.

    The renderer's own version is always available. Everything about the *run*
    comes from its run manifest, so those fields are filled only when one was
    supplied, and a collection spanning several runs reports none of them and
    says so instead of picking one.
    """

    #: Version of the package that rendered the page. Filled from installed
    #: metadata by default; ``None`` only in a source checkout.
    dev_version: str | None = Field(default_factory=_installed_version)
    #: Commit of the dev package that produced the *run*, per its manifest.
    dev_git_commit: str | None = None
    run_id: str | None = None
    run_created_at: str | None = None
    #: The decision settings the run used, already rendered, e.g.
    #: ``"L_match thresholds 3.0 / 2.0 / 1.0; epsilon 1e-15"``.
    configuration: str = ""
    #: Where the run settings were read from, normally a manifest path.
    source: str = ""
    #: True when the cases came from runs with differing provenance, so no
    #: single run can be named.
    several_runs: bool = False

    @property
    def lines(self) -> list[str]:
        """Footer lines, in order, omitting whatever is unknown."""
        out: list[str] = []
        if self.dev_version:
            out.append(f"vigiTwin {self.dev_version}")
        if self.several_runs:
            out.append("several pipeline runs; see each case")
            return out
        # One clause for the run, not two: the id names it and the date
        # places it, and a footer does not need seconds.
        day = (self.run_created_at or "")[:10]
        if self.run_id and day:
            out.append(f"run {self.run_id} of {day}")
        elif self.run_id:
            out.append(f"run {self.run_id}")
        elif day:
            out.append(f"run of {day}")
        if self.dev_git_commit:
            out.append(f"pipeline commit {self.dev_git_commit[:12]}")
        if self.configuration:
            out.append(self.configuration)
        if self.source:
            out.append(f"settings from {self.source}")
        return out


class EvidencePageVM(BaseModel):
    """One HTML document holding one or more cases."""

    title: str
    intro: str = ""
    note: str = ""
    generated_at: str = ""
    #: Always present, so the footer can state the rendering version even for
    #: a hand-built page; the run fields fill in when a manifest was supplied.
    provenance: ProvenanceVM = Field(default_factory=ProvenanceVM)
    cases: list[EvidenceCaseVM] = Field(default_factory=list)

    @property
    def gaps(self) -> list[EvidenceGap]:
        """Every case's gaps, deduplicated by code, in first-seen order."""
        seen: dict[EvidenceGapCode, EvidenceGap] = {}
        for case in self.cases:
            for gap in case.gaps:
                seen.setdefault(gap.code, gap)
        return list(seen.values())


class EvidenceIndexItem(BaseModel):
    """One row of the evidence index."""

    example_id: str
    #: The case's own label, so the index and the case agree about what to
    #: call it. Falls back to ``example_id`` in the template.
    example_label: str = ""
    pair_id: str
    filename: str
    title: str = ""
    kicker: str = ""
    final_label: str | None = None
    reference_label: str | None = None


class EvidenceIndexVM(BaseModel):
    """The index page over a set of generated evidence reviews."""

    title: str
    generated_at: str = ""
    items: list[EvidenceIndexItem] = Field(default_factory=list)


def number_cases(cases: Sequence[EvidenceCaseVM]) -> list[EvidenceCaseVM]:
    """Place each case in the collection the page renders, 1..N of N.

    Returns copies; the caller's cases are untouched. A case that already
    carries a position keeps it, which makes this idempotent and lets a
    curated collection's own numbering survive being written out one file per
    case.
    """
    total = len(cases)
    return [
        case
        if case.position is not None
        else case.model_copy(
            update={"position": ordinal, "position_total": total}
        )
        for ordinal, case in enumerate(cases, start=1)
    ]


__all__ = [
    "AEEvidenceRow",
    "AssessmentRowVM",
    "Attribution",
    "AttributionKind",
    "CharacteristicRow",
    "DiscrepancyVM",
    "DrugEntryVM",
    "DrugEvidenceRow",
    "EvidenceCaseVM",
    "EvidenceCell",
    "EvidenceGap",
    "EvidenceGapCode",
    "EvidenceIndexItem",
    "EvidenceIndexVM",
    "EvidencePageVM",
    "EvidenceRole",
    "EvidenceSource",
    "EvidenceTextVM",
    "FreqRowVM",
    "MatchingFeatureRow",
    "OutcomeTileVM",
    "PresenceVM",
    "ReconciliationVM",
    "ReferenceVerdictVM",
    "SharedSetVM",
    "TileState",
    "ValueKind",
    "missing_cell",
    "number_cases",
    "stronger_role",
]
