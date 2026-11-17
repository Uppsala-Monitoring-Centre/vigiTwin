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

    Green is what the assessment counted *for* the pair, red what it counted
    *against*, neutral what it did not speak to.

    ``ANCHOR`` and ``MATCHING`` are both green: the first is a member of the
    pattern that *defined* the anchor population, the second contributed
    additional matching evidence. They are kept apart because the distinction
    is real and the summary states it — an anchor-defining set does not also
    contribute a term to the matching score — even though they render alike.

    ``INCONSISTENT`` is red, and a retained inconsistency is the commonest but
    not the only thing it covers: the anchor population adjustment,
    ``-log10(C(N_anchor, 2))``, is a negative term that pushes the matching
    score down towards independence, so it counts against the pair too. Red
    says "this told against", not "this is a finding about a reported value".

    ``NEUTRAL`` covers everything the assessment did not act on: unscored
    agreements, differences it did not treat as inconsistencies, missing
    values, withheld values, and suppressed findings — the last of these
    because a suppressed finding contributed exactly zero and so told neither
    way.
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
#: over green: red is a claim the assessment made *against* the pair, whereas
#: matching evidence is additive. Both attributions stay on the cell, so a
#: tooltip can still show the match.
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


def sign_css_class(value: float) -> str:
    """The colour a signed figure in the score arithmetic takes.

    The same rule the rest of the page follows, applied to a number rather
    than to a reported value: a term that raised the matching score told for
    the pair and is green, one that lowered it told against and is red, and a
    term of exactly zero moved nothing and stays neutral.
    """
    if value > 0:
        return EvidenceRole.MATCHING.css_class
    if value < 0:
        return EvidenceRole.INCONSISTENT.css_class
    return EvidenceRole.NEUTRAL.css_class


def _is_number(token: str) -> bool:
    """Whether a token is a bare figure, so a unit may be factored off it."""
    try:
        float(token.replace(",", ""))
    except ValueError:
        return False
    return True


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
    #: A study, publication, protocol or linked-case reference that ties two
    #: reports to one underlying source. Its own kind rather than
    #: ``IDENTIFIER``: it names a *source*, not a report, so it is masked
    #: under its own policy and aliased in its own series.
    SOURCE_IDENTIFIER = "source_identifier"
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
    #: The multiplicity term ``-log10(C(N_anchor, 2))``. Not a finding about a
    #: reported value, but a negative term in the same arithmetic, so it takes
    #: the same red as one.
    ANCHOR_MULTIPLICITY = "anchor_multiplicity"


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
    #: Event-level onset dates, under each report's own term, as a drug's
    #: dates sit under its substance. They had a table of their own, keyed by
    #: term, which made a reader match an event against a second row to learn
    #: when it started.
    left_onsets: list[EvidenceCell] = Field(default_factory=list)
    right_onsets: list[EvidenceCell] = Field(default_factory=list)
    row_role: EvidenceRole = EvidenceRole.NEUTRAL
    sort_key: str = ""

    @property
    def has_onsets(self) -> bool:
        """Kept for callers; the page no longer selects rows by it."""
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
    #: Where a value is not the report's own words, a line saying so. The
    #: overall adverse-event onset is the case: a report may carry one
    #: explicitly, or the pipeline may derive it from the event and drug
    #: dates, and a derived value shown in a column headed "Report A" would
    #: otherwise read as something that report stated.
    left_note: str = ""
    right_note: str = ""
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
    #: True for the one scope the rarity was actually judged in. The page
    #: marks it here rather than describing it in a paragraph above the
    #: table: a reader looking for "which of these three counts was used"
    #: was being asked to match a sentence against a row.
    is_selected: bool = False
    #: Which count of the selected row defined the anchor population —
    #: ``"drugs"``, ``"aes"``, ``"both"``, or empty. Only ever set on the
    #: selected row, since the anchor is drawn from one scope.
    anchor_column: str = ""

    @property
    def row_css_class(self) -> str:
        """Tint for the scope that was used, neutral for the others."""
        return "scope-selected" if self.is_selected else ""

    def count_css_class(self, column: str) -> str:
        """Emphasis for the one count that became the anchor population.

        Deliberately not green or red. The anchor size is neither evidence
        for the pair nor against it — it is the population the rarity was
        measured in — and spending one of the page's two colours on it
        would make the colour rule mean two things.
        """
        return "anchor-count" if column == self.anchor_column else ""

    @property
    def drugs_css_class(self) -> str:
        return self.count_css_class("drugs")

    @property
    def aes_css_class(self) -> str:
        return self.count_css_class("aes")

    @property
    def both_css_class(self) -> str:
        return self.count_css_class("both")


class MatchingFeatureRow(BaseModel):
    """One additional matching feature and its contribution, in decades."""

    label: EvidenceCell
    contribution: float
    contribution_text: str

    @property
    def contribution_css_class(self) -> str:
        """The figure's own colour, so it agrees with the label beside it."""
        return sign_css_class(self.contribution)


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
    #: Whether the figures shown account for the score the run stored, either
    #: by agreeing with it or by sitting at a bound that explains the
    #: difference. False means the two genuinely disagree, which decides
    #: which of them the page is willing to quote.
    reconciled: bool = True

    @property
    def anchor_adjustment_css_class(self) -> str:
        """Red whenever the adjustment moved the score, which is downwards.

        ``C(N_anchor, 2) >= 1``, so the term is never positive; a lone
        two-report anchor gives exactly zero and the row is not shown at all.
        """
        if self.anchor_adjustment is None:
            return EvidenceRole.NEUTRAL.css_class
        return sign_css_class(self.anchor_adjustment)

    @property
    def additive_total_css_class(self) -> str:
        """The net of the column, coloured by which way it came out."""
        return sign_css_class(self.additive_total)


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
    #: The finding in prose, as the assessment described it. Shown where the
    #: two values below are not available — a drug-set or adverse-event
    #: finding has no single pair of values to put side by side.
    text: str
    #: The two values the finding is about, where it is about a pair of
    #: them. These are the *same cell objects* the characteristics table
    #: holds, so one publication rewrite reaches both and the finding cannot
    #: come to quote a value the table above it no longer shows. "Difference
    #: of 1 years." says what changed; "34 years vs 35 years" says what the
    #: reports actually said, which is what a reader is checking.
    left: EvidenceCell | None = None
    right: EvidenceCell | None = None
    #: What a finding says either side of its two values, where it says
    #: anything: ``Drug start date for tacrolimus`` before them and ``, at
    #: least 796 days apart`` after. Kept apart from the values so the
    #: findings table can colour the values and still print the sentence —
    #: an inconsistency about two dates showed neither of them in red, which
    #: is the one thing the page's colour rule exists to do.
    lead: str = ""
    tail: str = ""
    #: False only for a suppressed finding, which stayed in the record but
    #: contributed nothing to the penalty.
    retained: bool = True
    suppressed: bool = False
    #: True for a finding the source-context assessment produced. It is a
    #: scored inconsistency in the stored artifacts like any other, and where
    #: the source context *decided* the relationship it is not one in the
    #: decision trace: the decision was pinned on the verdict regardless of
    #: what the penalty came to. See ``EvidenceCaseVM.scored_discrepancies``.
    source_context: bool = False

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
    #: The finding reduced to the values it is about, for the panel's chips:
    #: ``"vitamins nos"``, ``"infection"``, ``"2002-10-28 vs 2005"``. Set
    #: only where the values are not already in ``left``/``right``. Empty
    #: falls back to ``text``, which is the full sentence.
    short_text: str = ""

    @property
    def chip_text(self) -> str:
        """The finding at a glance: the values, and nothing said about them.

        A chip has room for what the reports said and not for why it counts.
        "Age: 31 years vs 30 years" becomes "31 vs 30 years" — the feature is
        the column the findings table below carries, the colour already says
        it told against the pair, and a repeated unit is said once.

        Derived at render time from ``left``/``right`` rather than frozen at
        build time, so a publication rewrite of those cells reaches this too.
        """
        pair = self.short_pair
        if pair is not None:
            left, right, unit = pair
            return f"{left} vs {right} {unit}".strip()
        return self.short_text or self.text

    @property
    def short_pair(self) -> tuple[str, str, str] | None:
        """``(left, right, shared unit)``, or ``None`` with no pair of values.

        A unit is factored out only when both sides are bare numbers once it
        is removed, which is exactly the measurement case — ``31 years`` /
        ``30 years``. Anything else keeps its own words, so two country names
        that happen to end alike are never spliced together.
        """
        if self.left is None or self.right is None:
            return None
        left = (self.left.text or "").strip()
        right = (self.right.text or "").strip()
        if not left or not right:
            return None
        lhs, rhs = left.split(), right.split()
        unit: list[str] = []
        while (
            len(lhs) > 1
            and len(rhs) > 1
            and lhs[-1].casefold() == rhs[-1].casefold()
        ):
            unit.insert(0, lhs.pop())
            rhs.pop()
        if unit and not (_is_number(lhs[-1]) and _is_number(rhs[-1])):
            return left, right, ""
        return " ".join(lhs), " ".join(rhs), " ".join(unit)

    @property
    def unsigned_weight_text(self) -> str:
        """The weight as a magnitude: ``0.4``, not ``+0.4``.

        A penalty weight has no direction to signal — every one of them
        counts against the pair, which the chip's colour already says — and a
        ``+`` beside a red figure reads as a contradiction. The findings
        table keeps the signed form, where it sits in a column of figures.
        """
        return self.weight_text.lstrip("+")

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
        """Muted only where there is no verdict to show.

        ``INCONCLUSIVE`` is a verdict: somebody assessed the reference label
        and found it genuinely ambiguous. Styling it like an absence put
        "Genuinely ambiguous" in the small grey type reserved for "Not
        assessed", which reads as though nobody had looked.
        """
        shown = (TileState.VALUE, TileState.INCONCLUSIVE)
        return "tile-value" if self.state in shown else "tile-absent"


class DecisionStage(str, Enum):
    """Which of the two questions a decision tile reports."""

    INDEPENDENCE = "independence"
    RELATIONSHIP = "relationship"


class DecisionPathway(str, Enum):
    """What the decision actually turned on.

    The page used to let a reader infer this from which figures were filled
    in, which worked for the rarity route and not at all for the others: a
    pair decided on direct evidence of a shared source showed a matching
    score, an inconsistency penalty and no indication that neither had
    settled anything.
    """

    SHARED_SOURCE = "shared_source"
    #: The case labels *within* a shared source — matching ones putting the
    #: pair on one case, differing ones on two. Named apart from
    #: ``SHARED_SOURCE`` because a reader needs both halves: a shared source
    #: is what made the comparison possible, and the individual identifiers
    #: are what it turned on.
    INDIVIDUAL_IDENTIFIERS = "individual_identifiers"
    EXPLICIT_LINK = "explicit_link"
    PROBABILISTIC_MATCHING = "probabilistic_matching"
    INCONSISTENCY_PENALTY = "inconsistency_penalty"
    #: The question was never put, because independence was not rejected.
    NOT_REACHED = "not_reached"
    #: No classification was saved, so there is nothing to report.
    UNRECORDED = "unrecorded"


#: Reader-facing name for each pathway. Source-neutral and free of the
#: assessment's own field names: a reader is told what the decision rested
#: on, not which enum member recorded it.
_PATHWAY_LABEL: dict[DecisionPathway, str] = {
    DecisionPathway.SHARED_SOURCE: "Shared source",
    DecisionPathway.INDIVIDUAL_IDENTIFIERS: "Individual ids",
    DecisionPathway.EXPLICIT_LINK: "Explicit reference",
    DecisionPathway.PROBABILISTIC_MATCHING: "Probabilistic matching",
    DecisionPathway.INCONSISTENCY_PENALTY: "Inconsistency assessment",
    DecisionPathway.NOT_REACHED: "Not reached",
    DecisionPathway.UNRECORDED: "Not recorded",
}


def pathway_label(pathway: DecisionPathway) -> str:
    """The reader-facing badge text for a pathway."""
    return _PATHWAY_LABEL.get(pathway, "Not recorded")


class DecisionTileVM(BaseModel):
    """One of the two decision stages, as a tile.

    The two stages get the same shape and the same prominence whichever route
    each took, which is the point: direct evidence of a shared source decides
    as firmly as a rarity calculation, and a layout that gave the arithmetic a
    panel and the source context a sentence in passing told a reader
    otherwise.

    :attr:`basis` is the evidence that drove the decision. :attr:`role`
    carries which way the outcome went, so the tile is coloured by the page's
    one colour rule rather than by a vocabulary of its own.
    """

    stage: DecisionStage
    stage_label: str
    outcome: str
    state: TileState = TileState.VALUE
    pathway: DecisionPathway = DecisionPathway.UNRECORDED
    #: Which way this outcome points: green where it counts towards the
    #: reports being one case, red where it counts against. The same two
    #: colours the rest of the page uses, which is the point — a reader who
    #: has learned the colour rule on the tables can read these without
    #: learning a second one. ``NEUTRAL`` for an abstention or an absence,
    #: which point neither way.
    role: EvidenceRole = EvidenceRole.NEUTRAL
    #: The decision-driving evidence in a phrase: "Differing comparable
    #: individual identifiers", "Matching score 4.2". Empty where it would
    #: only repeat :attr:`pathway_label`.
    basis: str = ""
    #: The threshold, the tolerance or the reason.
    note: str = ""

    @property
    def pathway_label(self) -> str:
        return pathway_label(self.pathway)

    @property
    def css_class(self) -> str:
        """Tile classes: present or muted, and which way the outcome went."""
        present = (
            "decision-value"
            if self.state is TileState.VALUE
            else "decision-absent"
        )
        return f"{present} {self.role.css_class}"

    @property
    def pathway_css_class(self) -> str:
        """The badge, coloured by the outcome rather than by the route.

        A badge coloured per pathway said that a shared-source decision and a
        rarity decision were different *kinds* of thing, which is not the
        distinction a reader needs from a colour. Which way the decision went
        is.
        """
        return f"pathway {self.role.css_class}"


class SourceContextVM(BaseModel):
    """What the narratives said about a shared source, and about the cases in it.

    Held apart from the inconsistency findings because it is a different kind
    of evidence: a shared source identifier is a fact about provenance, and
    where the verdict is definitive the relationship follows from it directly
    rather than from a weighed penalty.

    The identifier is an :class:`EvidenceCell` and the prose names no
    identifier, which is the same division the reporting context already
    keeps: values live in cells, where a publication transform can reach them
    by kind, and sentences stay the same in both presentations.
    """

    #: The verdict in the reader's terms — never the stored enum value.
    verdict_label: str
    #: The shared source identifier, where the comparison found one.
    identifier: EvidenceCell | None = None
    #: What kind of source the identifier names: "Study name", "Trial
    #: registration", "Publication (DOI)".
    identifier_label: str = ""
    #: Whether the cases *within* that source were compared, and what came of
    #: it. The distinction the verdict rests on, stated rather than implied.
    case_comparison: str = ""
    #: True when this assessment determined the relationship classification.
    decided_relationship: bool = False
    role: EvidenceRole = EvidenceRole.NEUTRAL

    @property
    def css_class(self) -> str:
        return self.role.css_class


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
    REFERENCE_LABEL_UNREADABLE = "reference_label_unreadable"
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


class CaseSummaryVM(BaseModel):
    """The assessment chain in figures, for the panel above the narrative.

    The same slots the generated paragraph fills in prose — context, anchor,
    additional matching evidence, the independence decision, the findings, the
    same-case decision — which is what :class:`~.summary.SummarySentence`
    being tagged by slot was for: a table and a paragraph rendering the same
    assessment and unable to drift apart.

    It holds no copy of the contributions or the findings. The template reads
    ``EvidenceCaseVM.matching_features`` and ``retained_discrepancies``
    directly, so the panel and the tables below it are the same objects rather
    than two derivations of one fact.

    The two decision rows carry their clause as well as their figure. That is
    not decoration: these clauses — the independence threshold and zone, the
    penalty against its tolerance, and the reason no comparison happened where
    none did — used to live in a decision table in section 3, and a case
    presented with a human interpretation instead of the generated summary
    would otherwise state no threshold anywhere on the page.
    """

    #: The population the rarity was judged against, named: the country and
    #: year a country-year run used, the country alone, or ``Global``. A cell
    #: rather than a string because a country is a report value, and the
    #: later publication phase masks or substitutes it like any other.
    context: EvidenceCell | None = None
    #: The anchor-defining sets, as the same :class:`SharedSetVM` objects the
    #: section below holds — so one publication rewrite of a member's ``text``
    #: reaches both, and the set boundary survives: a drugs-and-AEs anchor
    #: reads ``levonorgestrel + device expulsion``, not one flat list.
    anchor_sets: list[SharedSetVM] = Field(default_factory=list)
    anchor_n_text: str = ""
    #: ``-log10(C(N_anchor, 2))``, as a cell so its red comes from a role like
    #: every other coloured thing on the page. ``None`` when it is zero.
    anchor_adjustment: EvidenceCell | None = None
    score_text: str = ""
    #: Why independence was or was not rejected, from the same clause builder
    #: the narrative paragraph uses.
    independence_note: str = ""
    #: The penalty arithmetic: ``3 x 0.4 = 1.2``, then the comparison it was
    #: put to, or ``(not weighed)`` where it was put to none.
    penalty_text: str = ""
    same_case_note: str = ""

    @property
    def is_empty(self) -> bool:
        """True when no row has a value, in which case no panel is rendered."""
        return not any(
            (
                self.context,
                self.anchor_sets,
                self.anchor_n_text,
                self.anchor_adjustment,
                self.score_text,
                self.penalty_text,
            )
        )


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
    #: The two decision stages, in order. Always both, whichever route each
    #: took: a reader comparing two cases should find the same two tiles in
    #: the same place and read the difference off the pathway.
    decisions: list[DecisionTileVM] = Field(default_factory=list)
    reference_verdict: ReferenceVerdictVM | None = None
    #: The at-a-glance panel, rendered between the verdict note and the
    #: written summary. ``None`` when the stored artifacts support no row.
    summary_panel: CaseSummaryVM | None = None

    #: The human reading, when one was supplied, and/or the deterministic
    #: summary. Rendered in list order.
    narrative_blocks: list[EvidenceTextVM] = Field(default_factory=list)

    characteristics: list[CharacteristicRow] = Field(default_factory=list)
    #: Why this reporting context applied, in prose. Built and kept for
    #: consumers; the page no longer prints it, because it described the
    #: context table below it and that table now marks the selected scope
    #: itself.
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
    #: The anchor and its size, in prose. Built and kept for consumers; the
    #: page no longer prints it — the panel names the anchor and the context
    #: table boxes its count. Dropped for ``context_note``'s reason: it was
    #: a sentence a reader had to match against a row.
    anchor_note: str = ""
    freq_rows: list[FreqRowVM] = Field(default_factory=list)
    matching_features: list[MatchingFeatureRow] = Field(default_factory=list)
    reconciliation: ReconciliationVM | None = None

    explicit_link_note: str = ""
    #: The narrative-derived source and case verdict, where one was reached.
    #: Prose only: the identifier itself is in ``source_context``.
    source_case_note: str = ""
    #: The source-context evidence, as structure rather than as a sentence.
    source_context: SourceContextVM | None = None
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
        """Every finding the assessment retained. The audit view, unfiltered."""
        return [d for d in self.discrepancies if d.retained]

    @property
    def suppressed_discrepancies(self) -> list[DiscrepancyVM]:
        return [d for d in self.discrepancies if not d.retained]

    @property
    def source_context_decided(self) -> bool:
        """Whether a definitive source-context verdict settled the relationship."""
        return (
            self.source_context is not None
            and self.source_context.decided_relationship
        )

    @property
    def scored_discrepancies(self) -> list[DiscrepancyVM]:
        """The findings the decision trace presents as weighed inconsistencies.

        The same as :attr:`retained_discrepancies`, except where the source
        context decided the relationship. There the source-derived finding is
        not a term in a comparison that happened — the verdict pinned the
        outcome whatever the penalty came to — so listing it in a table of
        weighed severities, with a weight beside it, says the arithmetic
        reached the conclusion. The finding keeps its severity and its weight
        in :attr:`discrepancies` and in every stored artifact; what changes is
        where the page shows it, which is the source-context block.
        """
        if not self.source_context_decided:
            return self.retained_discrepancies
        return [d for d in self.retained_discrepancies if not d.source_context]

    @property
    def deferred_discrepancies(self) -> list[DiscrepancyVM]:
        """Retained findings shown as source-context evidence instead."""
        if not self.source_context_decided:
            return []
        return [d for d in self.retained_discrepancies if d.source_context]


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


class PresentationMode(str, Enum):
    """Which presentation a page is — and so what it may be shown to.

    ``INTERNAL`` is the default everywhere, which is the point: the existing
    presentation is unchanged, and a page becomes a publication candidate
    only by being put through
    :func:`llm_dup_dev.review_html.evidence.publication.scramble_page`. The
    footer states the mode, so the two cannot be mistaken for one another
    once they are separate files on a disk.

    ``PUBLICATION`` says the displayed values have been transformed. It does
    not say the page is anonymous: that is a disclosure reviewer's
    conclusion, never an attribute a renderer can set.
    """

    INTERNAL = "internal"
    PUBLICATION = "publication"


class EvidencePageVM(BaseModel):
    """One HTML document holding one or more cases."""

    title: str
    intro: str = ""
    note: str = ""
    generated_at: str = ""
    #: Internal by default. The footer branches on it, and nothing else does:
    #: a publication page is the same template over transformed cases.
    mode: PresentationMode = PresentationMode.INTERNAL
    #: What the publication transform actually did, for the footer. Set by
    #: the publication layer from the effective policy, because the policy is
    #: configurable: a footer naming every attribute category would be wrong
    #: for any run that left one of them as reported.
    transform_note: str = ""
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
    "CaseSummaryVM",
    "CharacteristicRow",
    "DecisionPathway",
    "DecisionStage",
    "DecisionTileVM",
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
    "PresentationMode",
    "ReconciliationVM",
    "ReferenceVerdictVM",
    "SharedSetVM",
    "SourceContextVM",
    "TileState",
    "ValueKind",
    "missing_cell",
    "number_cases",
    "pathway_label",
    "sign_css_class",
    "stronger_role",
]
