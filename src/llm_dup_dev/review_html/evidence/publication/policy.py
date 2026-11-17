"""What may be shown, what must be transformed, and what must be reviewed.

Three tables and one settings object, kept apart from the code that applies
them so the decisions are readable on their own:

* :data:`KIND_POLICIES` — one entry per :class:`~..models.ValueKind`, saying
  how a displayed value of that class is handled. Exhaustive **by test**: a
  kind added to the model with no entry here fails the suite rather than
  reaching a publication page untransformed.
* :data:`FREE_TEXT_FIELDS` — one entry per string-bearing field of the
  evidence view model. Also exhaustive by test, for the same reason.
* :class:`PublicationPolicy` — the numeric and naming settings, versioned so
  an artifact can say which policy produced it.

Nothing here is sensitive. The salt lives in :class:`PublicationSecrets`,
which is loaded from the environment and never serialised.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from enum import Enum
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from ..models import ValueKind

#: Environment variable holding the publication salt. There is deliberately
#: no default: a default salt would make every offset in every published
#: supplement guessable by anyone holding this source.
SALT_ENV_VAR = "VIGITWIN_PUBLICATION_SALT"


class ValuePolicy(str, Enum):
    """How a displayed value of one :class:`ValueKind` is handled.

    ``KEEP`` is a positive statement, not a default: drug substances, adverse
    event terms, sex, counts and scores are *retained* because they carry the
    clinical and evidential meaning the presentation exists to convey, and
    because none of them is identifying on its own.
    """

    KEEP = "keep"
    SHIFT_DATE = "shift_date"
    OFFSET_AGE = "offset_age"
    OFFSET_MEASUREMENT = "offset_measurement"
    ALIAS_COUNTRY = "alias_country"
    ALIAS_IDENTIFIER = "alias_identifier"
    ALIAS_SOURCE = "alias_source"
    #: The cell's content is not a single typed value — it is whatever prose
    #: the assessment supplied. Handled by the free-text rules, which means
    #: substitution followed by a residual scan, never a silent pass-through.
    REVIEW_FREE_TEXT = "review_free_text"


#: Every ``ValueKind``, and what happens to a cell carrying it.
KIND_POLICIES: Mapping[ValueKind, ValuePolicy] = {
    # Retained: clinically relevant and not identifying on its own.
    ValueKind.SEX: ValuePolicy.KEEP,
    ValueKind.DRUG_SUBSTANCE: ValuePolicy.KEEP,
    ValueKind.DRUG_ROLE: ValuePolicy.KEEP,
    ValueKind.DRUG_FORM: ValuePolicy.KEEP,
    ValueKind.DRUG_STRENGTH: ValuePolicy.KEEP,
    ValueKind.AE_TERM: ValuePolicy.KEEP,
    # Retained because the instruction is to keep contextual frequencies,
    # scores, contributions and penalties exactly as the assessment produced
    # them. See the limitations section of docs/evidence_publication_mode.md.
    ValueKind.COUNT: ValuePolicy.KEEP,
    ValueKind.SCORE: ValuePolicy.KEEP,
    # Transformed.
    ValueKind.REPORT_DATE: ValuePolicy.SHIFT_DATE,
    ValueKind.DRUG_START_DATE: ValuePolicy.SHIFT_DATE,
    ValueKind.DRUG_END_DATE: ValuePolicy.SHIFT_DATE,
    ValueKind.AE_ONSET_DATE: ValuePolicy.SHIFT_DATE,
    ValueKind.AGE: ValuePolicy.OFFSET_AGE,
    ValueKind.HEIGHT: ValuePolicy.OFFSET_MEASUREMENT,
    ValueKind.WEIGHT: ValuePolicy.OFFSET_MEASUREMENT,
    ValueKind.COUNTRY: ValuePolicy.ALIAS_COUNTRY,
    ValueKind.IDENTIFIER: ValuePolicy.ALIAS_IDENTIFIER,
    ValueKind.SOURCE_IDENTIFIER: ValuePolicy.ALIAS_SOURCE,
    # Prose in a cell.
    ValueKind.FREE_TEXT: ValuePolicy.REVIEW_FREE_TEXT,
}


class FreeTextPolicy(str, Enum):
    """How one string-bearing field of the view model is handled."""

    #: Built only from non-identifying material — a label, a severity, a
    #: formatted figure. Left alone, then checked by the residual scan like
    #: everything else.
    PASS_THROUGH = "pass_through"
    #: Derived prose that quotes report values. Rewritten through the case's
    #: substitution table, then checked by the residual scan.
    SUBSTITUTE = "substitute"
    #: An identifier or a path, replaced wholesale by a deterministic alias
    #: that carries none of the original. Nothing of the original survives, so
    #: there is nothing for a reviewer to weigh.
    ALIAS = "alias"
    #: Human-authored or verbatim narrative text. Never auto-redacted: it
    #: needs an approved replacement, an explicit decision to withhold, or it
    #: blocks the export.
    REVIEW = "review"


#: Fields that hold a *figure* — a count, a score, a contribution, a weight —
#: rather than anything a report said. The residual scan does not look for
#: date-shaped tokens in them: a co-occurrence count of 2019 reports is not a
#: year, and reporting it as one buries the findings that matter in noise. It
#: still looks for original *values* there, which is the check that would
#: catch a date written into one of them by mistake.
NUMERIC_TEXT_FIELDS: frozenset[str] = frozenset(
    {
        "FreqRowVM.n_drugs",
        "FreqRowVM.n_aes",
        "FreqRowVM.n_both",
        "MatchingFeatureRow.contribution_text",
        "ReconciliationVM.contributions_total_text",
        "ReconciliationVM.anchor_adjustment_text",
        "ReconciliationVM.additive_total_text",
        "ReconciliationVM.stored_score_text",
        "DiscrepancyVM.weight_text",
        "CaseSummaryVM.anchor_n_text",
        "CaseSummaryVM.score_text",
    }
)

#: Every string-bearing field of the evidence view model, keyed
#: ``"ClassName.field"``. Exhaustive by test — see
#: ``tests/test_review_html/test_publication_findings.py``.
FREE_TEXT_FIELDS: Mapping[str, FreeTextPolicy] = {
    # --- labels and formatted figures: nothing of the patient in them ------
    "PresenceVM.label": FreeTextPolicy.PASS_THROUGH,
    "SharedSetVM.label": FreeTextPolicy.PASS_THROUGH,
    "CharacteristicRow.label": FreeTextPolicy.PASS_THROUGH,
    "FreqRowVM.n_drugs": FreeTextPolicy.PASS_THROUGH,
    "FreqRowVM.n_aes": FreeTextPolicy.PASS_THROUGH,
    "FreqRowVM.n_both": FreeTextPolicy.PASS_THROUGH,
    "FreqRowVM.anchor_column": FreeTextPolicy.PASS_THROUGH,
    "MatchingFeatureRow.contribution_text": FreeTextPolicy.PASS_THROUGH,
    "ReconciliationVM.contributions_total_text": FreeTextPolicy.PASS_THROUGH,
    "ReconciliationVM.anchor_adjustment_text": FreeTextPolicy.PASS_THROUGH,
    "ReconciliationVM.additive_total_text": FreeTextPolicy.PASS_THROUGH,
    "ReconciliationVM.stored_score_text": FreeTextPolicy.PASS_THROUGH,
    "DiscrepancyVM.feature_label": FreeTextPolicy.PASS_THROUGH,
    "DiscrepancyVM.severity": FreeTextPolicy.PASS_THROUGH,
    "DiscrepancyVM.weight_text": FreeTextPolicy.PASS_THROUGH,
    "AssessmentRowVM.label": FreeTextPolicy.PASS_THROUGH,
    "OutcomeTileVM.label": FreeTextPolicy.PASS_THROUGH,
    "OutcomeTileVM.value": FreeTextPolicy.PASS_THROUGH,
    "DecisionTileVM.stage_label": FreeTextPolicy.PASS_THROUGH,
    "DecisionTileVM.outcome": FreeTextPolicy.PASS_THROUGH,
    # Reader-facing labels by construction: a verdict label names the kind of
    # evidence, never the identifier, which lives in a cell of its own.
    "SourceContextVM.verdict_label": FreeTextPolicy.PASS_THROUGH,
    "SourceContextVM.identifier_label": FreeTextPolicy.PASS_THROUGH,
    "EvidencePageVM.transform_note": FreeTextPolicy.PASS_THROUGH,
    "ReferenceVerdictVM.reference_label": FreeTextPolicy.PASS_THROUGH,
    "ReferenceVerdictVM.display": FreeTextPolicy.PASS_THROUGH,
    "ReferenceVerdictVM.set_label": FreeTextPolicy.PASS_THROUGH,
    "ReferenceVerdictVM.positive_class_name": FreeTextPolicy.PASS_THROUGH,
    "ReferenceVerdictVM.comparison_note": FreeTextPolicy.PASS_THROUGH,
    "EvidenceTextVM.heading": FreeTextPolicy.PASS_THROUGH,
    "EvidenceGap.message": FreeTextPolicy.PASS_THROUGH,
    "EvidenceCell.text": FreeTextPolicy.PASS_THROUGH,  # handled by KIND_POLICIES
    "DrugEvidenceRow.sort_key": FreeTextPolicy.PASS_THROUGH,
    "AEEvidenceRow.sort_key": FreeTextPolicy.PASS_THROUGH,
    "EvidencePageVM.title": FreeTextPolicy.PASS_THROUGH,
    "EvidencePageVM.generated_at": FreeTextPolicy.PASS_THROUGH,
    "ProvenanceVM.dev_version": FreeTextPolicy.PASS_THROUGH,
    "ProvenanceVM.dev_git_commit": FreeTextPolicy.PASS_THROUGH,
    "ProvenanceVM.run_id": FreeTextPolicy.PASS_THROUGH,
    "ProvenanceVM.run_created_at": FreeTextPolicy.PASS_THROUGH,
    "ProvenanceVM.configuration": FreeTextPolicy.PASS_THROUGH,
    "Attribution.feature": FreeTextPolicy.PASS_THROUGH,
    "Attribution.severity": FreeTextPolicy.PASS_THROUGH,
    # --- derived prose that quotes values ---------------------------------
    "Attribution.note": FreeTextPolicy.SUBSTITUTE,
    "CharacteristicRow.left_note": FreeTextPolicy.SUBSTITUTE,
    "CharacteristicRow.right_note": FreeTextPolicy.SUBSTITUTE,
    "FreqRowVM.scope_label": FreeTextPolicy.SUBSTITUTE,
    "DiscrepancyVM.text": FreeTextPolicy.SUBSTITUTE,
    "DiscrepancyVM.lead": FreeTextPolicy.SUBSTITUTE,
    "DiscrepancyVM.tail": FreeTextPolicy.SUBSTITUTE,
    "DiscrepancyVM.short_text": FreeTextPolicy.SUBSTITUTE,
    "DiscrepancyVM.suppressed_by": FreeTextPolicy.SUBSTITUTE,
    "DiscrepancyVM.downgrades": FreeTextPolicy.SUBSTITUTE,
    "AssessmentRowVM.text": FreeTextPolicy.SUBSTITUTE,
    "DecisionTileVM.basis": FreeTextPolicy.SUBSTITUTE,
    "DecisionTileVM.note": FreeTextPolicy.SUBSTITUTE,
    "SourceContextVM.case_comparison": FreeTextPolicy.SUBSTITUTE,
    "CaseSummaryVM.anchor_n_text": FreeTextPolicy.SUBSTITUTE,
    "CaseSummaryVM.score_text": FreeTextPolicy.SUBSTITUTE,
    "CaseSummaryVM.independence_note": FreeTextPolicy.SUBSTITUTE,
    "CaseSummaryVM.penalty_text": FreeTextPolicy.SUBSTITUTE,
    "CaseSummaryVM.same_case_note": FreeTextPolicy.SUBSTITUTE,
    "EvidenceCaseVM.context_note": FreeTextPolicy.SUBSTITUTE,
    "EvidenceCaseVM.anchor_note": FreeTextPolicy.SUBSTITUTE,
    "EvidenceCaseVM.explicit_link_note": FreeTextPolicy.SUBSTITUTE,
    "EvidenceCaseVM.source_case_note": FreeTextPolicy.SUBSTITUTE,
    "EvidenceCaseVM.narrative_note": FreeTextPolicy.SUBSTITUTE,
    "EvidencePageVM.intro": FreeTextPolicy.SUBSTITUTE,
    "EvidencePageVM.note": FreeTextPolicy.SUBSTITUTE,
    # --- identifiers and paths: replaced wholesale ------------------------
    "EvidenceCaseVM.pair_id": FreeTextPolicy.ALIAS,
    "EvidenceCaseVM.example_id": FreeTextPolicy.ALIAS,
    #: The curator's citation label. Replaced by the positional alias rather
    #: than reviewed: a label naming a case is exactly where a report number
    #: or a country ends up, and nothing of the original is wanted.
    "EvidenceCaseVM.example_label": FreeTextPolicy.ALIAS,
    # A filesystem path, which may name a study, a share or a person.
    "ProvenanceVM.source": FreeTextPolicy.ALIAS,
    # --- never auto-handled ------------------------------------------------
    "EvidenceTextVM.paragraphs": FreeTextPolicy.REVIEW,
    "CharacteristicRow.left_evidence": FreeTextPolicy.REVIEW,
    "CharacteristicRow.right_evidence": FreeTextPolicy.REVIEW,
    "EvidenceCaseVM.title": FreeTextPolicy.REVIEW,
    "EvidenceCaseVM.lede": FreeTextPolicy.REVIEW,
    "EvidenceCaseVM.kicker": FreeTextPolicy.REVIEW,
}


class AgeBand(NamedTuple):
    """One coarse age band, in years, half-open at the top.

    An offset must not move a patient out of their band: a transform that
    turns a 17-year-old into a 19-year-old, or an infant into a toddler, has
    changed the clinical reading of the case, which is exactly what this
    layer must not do.
    """

    label: str
    lower_years: float
    upper_years: float


DEFAULT_AGE_BANDS: tuple[AgeBand, ...] = (
    AgeBand("infant", 0.0, 2.0),
    AgeBand("child", 2.0, 12.0),
    AgeBand("adolescent", 12.0, 18.0),
    AgeBand("adult", 18.0, 65.0),
    AgeBand("elderly", 65.0, 200.0),
)


class PublicationPolicy(BaseModel):
    """Numeric and naming settings for the publication transform.

    Versioned, and the version is recorded in both emitted artifacts: two
    supplements produced under different policies are not comparable, and a
    reader has no other way to tell.
    """

    model_config = ConfigDict(extra="forbid")

    policy_version: str = "1.0"

    #: Whether a transform may leave a value as reported. **On by default,
    #: and the default is the point.** A transform obliged to move every
    #: value tells anyone holding this policy that the displayed value is not
    #: the true one, which narrows the field they have to search. With zero
    #: among the candidate offsets, a published value and a reported one are
    #: states an observer cannot tell apart.
    #:
    #: Turning it off restores a transform that must move something, and with
    #: it the possibility that no candidate satisfies the invariants — which
    #: is why the rejection paths exist. The cost is the inference above.
    allow_unchanged_values: bool = True

    # --- dates -------------------------------------------------------------
    #: Smallest candidate offset. **Zero by default, and zero is a candidate
    #: like any other.** What an observer is left with is the *width* of the
    #: range, and every offset excluded from it is one they can rule out: a
    #: floor of 21 days told anyone holding this policy that the published
    #: date is at least three weeks from the true one, which is information
    #: about the true one.
    date_offset_min_days: int = Field(default=0, ge=0)
    #: Widest candidate offset. Equal to the floor it pins the offset to one
    #: value, which a test uses to reach the all-unchanged state on purpose.
    date_offset_max_days: int = Field(default=400, ge=0)
    #: How many candidate offsets to try before giving up on a case and
    #: flagging it for manual handling. The default exceeds the size of the
    #: pool, so the search is exhaustive and a case is never flagged merely
    #: because a sample missed the one offset that would have worked. The
    #: whole pool costs well under a tenth of a second per case.
    date_offset_candidates: int = Field(default=1024, ge=1)
    date_year_floor: int = Field(default=1960, ge=1)
    date_year_ceiling: int = Field(default=2100, ge=1)
    #: Separations at or below this many days are preserved to the day. The
    #: default covers the whole minor band, so a one-day discrepancy between
    #: two onsets — which is evidence a reader weighs directly — can never be
    #: rounded into something else.
    separation_exact_days: int = Field(default=7, ge=0)
    #: How far a larger separation may move, as a fraction of itself. A
    #: finding saying two dates are "at least 796 days apart" is making a
    #: claim about an order of magnitude and a direction, not about 796; the
    #: band check below is what holds the claim's meaning in place.
    separation_relative_tolerance: float = Field(default=0.20, ge=0.0)

    # --- ages and measurements --------------------------------------------
    age_offset_max_years: int = Field(default=3, ge=1)
    age_offset_max_months: int = Field(default=6, ge=1)
    age_offset_max_days: int = Field(default=20, ge=1)
    age_bands: tuple[AgeBand, ...] = DEFAULT_AGE_BANDS
    measurement_offset_max_kg: float = Field(default=3.0, gt=0)
    measurement_offset_max_cm: float = Field(default=4.0, gt=0)
    #: Whole-pound and whole-inch offsets, for values that were converted
    #: from imperial units before they reached the database.
    measurement_offset_max_lb: int = Field(default=6, ge=1)
    measurement_offset_max_in: int = Field(default=2, ge=1)

    # --- aliases -----------------------------------------------------------
    #: Whether countries are replaced by positional aliases. **Off by
    #: default.** A country is part of the reporting context vigiTwin judged
    #: rarity in, and in a study whose reference set was drawn by country the
    #: countries are known from the design — so masking them costs the reader
    #: the population the counts belong to and buys little. Turn it on where
    #: the country is not already public, or where a country and a small
    #: contextual count together narrow the population too far.
    #:
    #: A single example can be decided differently through
    #: :class:`CountryMaskingOverride`.
    mask_countries: bool = False
    country_alias_template: str = "Country {n}"
    #: Whether a shared source identifier — a study name, a trial
    #: registration, a DOI, a linked-case reference — is replaced by an
    #: alias. **On by default, and decided separately from the country.** A
    #: country is a population; a named study or publication is a small,
    #: often public set of patients, and naming it alongside a drug, an event
    #: and two dates narrows the field much further than a country does.
    #: There is no equivalent of "the countries are known from the reference
    #: set design" for a source identifier.
    mask_source_identifiers: bool = True
    #: Sources are aliased in their own series, not the identifier series:
    #: "Source 1" says what kind of thing was masked, and a reader following
    #: a shared source across the two reports of a pair needs to see that the
    #: two mentions are one source.
    source_alias_template: str = "Source {n}"
    identifier_alias_template: str = "Identifier {n}"
    pair_alias_template: str = "Pair {n}"
    report_alias_labels: tuple[str, str] = ("Report A", "Report B")
    #: What replaces ``ProvenanceVM.source``, which is a filesystem path.
    provenance_source_label: str = "secure study environment"

    # --- free text ---------------------------------------------------------
    #: What a withheld free-text block says in its place. A reader must be
    #: able to tell "nobody wrote this" from "this was not published", and a
    #: silently empty section says the first while meaning the second.
    withheld_text_notice: str = "Withheld from the published version."
    #: What happens to a ``REVIEW`` field with no approved replacement and no
    #: withhold decision. ``REVIEW`` means "block", which is the point.
    free_text_default: FreeTextPolicy = FreeTextPolicy.REVIEW
    #: Whether advisory findings block the export. Off by default, and the
    #: distinction is deliberate: an advisory says a relationship the page
    #: never states has moved, so nothing has leaked and there is no
    #: disclosure risk to fail closed about. Blocking on them would stop
    #: almost every real pair, because almost every real pair mixes a
    #: year-only date with a complete one. They are reported either way.
    advisories_block: bool = False

    @model_validator(mode="after")
    def _check_ranges(self) -> PublicationPolicy:
        if self.date_offset_min_days > self.date_offset_max_days:
            raise ValueError(
                "date_offset_min_days must not exceed date_offset_max_days"
            )
        if self.separation_relative_tolerance > 1.0:
            raise ValueError(
                "separation_relative_tolerance above 1.0 would let a "
                "separation change sign"
            )
        if self.date_year_floor >= self.date_year_ceiling:
            raise ValueError("date_year_floor must be below date_year_ceiling")
        if not self.age_bands:
            raise ValueError("age_bands must not be empty")
        if self.free_text_default is FreeTextPolicy.PASS_THROUGH:
            raise ValueError(
                "free_text_default must not be pass_through: human text is "
                "never published without review"
            )
        return self

    def age_band(self, years: float) -> str:
        """The band *years* falls in, or ``"out_of_range"``."""
        for band in self.age_bands:
            if band.lower_years <= years < band.upper_years:
                return band.label
        return "out_of_range"


class PublicationSecrets(BaseModel):
    """The salt every offset is derived from.

    Held as a :class:`SecretStr` so it cannot be logged or serialised by
    accident, and never written into any artifact. What *is* written is
    :attr:`key_id`, a keyed digest that identifies which salt was used without
    revealing it, so two supplements can be told apart without either of them
    carrying the means to reverse its own transform.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    salt: SecretStr

    @classmethod
    def from_env(cls, var: str = SALT_ENV_VAR) -> PublicationSecrets:
        """Load the salt from the environment, or say plainly that it is not set."""
        import os

        value = os.environ.get(var, "")
        if not value.strip():
            raise ValueError(
                f"{var} is not set. The publication transform has no default "
                "salt: a default would make every offset guessable from this "
                "source alone. Set it from the secure key store."
            )
        return cls(salt=SecretStr(value))

    @property
    def key_id(self) -> str:
        """A non-invertible 16-hex identifier for this salt."""
        digest = hmac.new(
            self.salt.get_secret_value().encode("utf-8"),
            b"vigitwin-publication-key-id",
            hashlib.sha256,
        )
        return digest.hexdigest()[:16]

    def seed(self, *parts: str) -> str:
        """A deterministic seed string for one draw.

        Colon-joined, in the house style of
        :mod:`llm_dup_dev.study_eval.sampling`: adding a new draw cannot
        perturb an existing one, because each gets its own namespace.
        """
        return ":".join((self.salt.get_secret_value(), *parts))


class ApprovedText(BaseModel):
    """Replacement text a reviewer has approved for one free-text field.

    The only way human-authored or verbatim narrative text reaches a
    publication page. ``field_path`` is the dotted path the transform reports
    in its finding, so a reviewer can copy it straight across.
    """

    model_config = ConfigDict(extra="forbid")

    example_id: str
    field_path: str
    #: The approved text. Empty means "withhold": the block is dropped and the
    #: page says so, which is different from the field having been empty.
    replacement: str = ""
    withhold: bool = False
    approved_by: str
    approved_at: str
    rationale: str = ""

    @model_validator(mode="after")
    def _check_decision(self) -> ApprovedText:
        if self.withhold and self.replacement:
            raise ValueError(
                "a withheld field has no replacement text; choose one"
            )
        if not self.withhold and not self.replacement.strip():
            raise ValueError(
                "replacement must be non-empty unless withhold is set"
            )
        return self


class CountryMaskingOverride(BaseModel):
    """One example's country-masking decision, against the policy default.

    The same shape as the other per-example decisions in this module: who
    decided, when, and why. A reviewer deciding that one example's country
    narrows its population too far does not have to change the policy for the
    whole collection.
    """

    model_config = ConfigDict(extra="forbid")

    example_id: str
    mask: bool
    decided_by: str
    decided_at: str
    rationale: str = ""


class FindingResolution(BaseModel):
    """A reviewer's decision that one disclosure finding may be published.

    Keyed the same way the finding is reported. A resolution is an audit
    record, not a switch: it names who accepted the risk and why.
    """

    model_config = ConfigDict(extra="forbid")

    code: str
    example_id: str
    field_path: str = ""
    approved_by: str
    approved_at: str
    rationale: str


class PublicationApprovals(BaseModel):
    """Everything a reviewer has signed off for one publication run.

    Lives on the secure side: the replacement texts are written against real
    cases, and the rationales name them.
    """

    model_config = ConfigDict(extra="forbid")

    approved_texts: list[ApprovedText] = Field(default_factory=list)
    resolutions: list[FindingResolution] = Field(default_factory=list)
    country_masking: list[CountryMaskingOverride] = Field(default_factory=list)

    def text_for(self, example_id: str, field_path: str) -> ApprovedText | None:
        for entry in self.approved_texts:
            if entry.example_id == example_id and entry.field_path == field_path:
                return entry
        return None

    def country_masking_for(self, example_id: str) -> bool | None:
        """This example's masking decision, or ``None`` to take the policy's."""
        for entry in self.country_masking:
            if entry.example_id == example_id:
                return entry.mask
        return None

    def resolution_for(
        self, code: str, example_id: str, field_path: str
    ) -> FindingResolution | None:
        for entry in self.resolutions:
            if entry.code != code or entry.example_id != example_id:
                continue
            if entry.field_path and entry.field_path != field_path:
                continue
            return entry
        return None


__all__ = [
    "DEFAULT_AGE_BANDS",
    "FREE_TEXT_FIELDS",
    "KIND_POLICIES",
    "NUMERIC_TEXT_FIELDS",
    "SALT_ENV_VAR",
    "AgeBand",
    "ApprovedText",
    "CountryMaskingOverride",
    "FindingResolution",
    "FreeTextPolicy",
    "PublicationApprovals",
    "PublicationPolicy",
    "PublicationSecrets",
    "ValuePolicy",
]
