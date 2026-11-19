"""Disclosure findings: what stopped, or might stop, a page being published.

Every finding blocks the publication export. That is the whole mechanism: a
finding is not a warning to be read past but a statement that this page is
not ready, and the only way past one is a :class:`~.policy.FindingResolution`
naming a reviewer and a reason, or an approved replacement text.

The codes fall into four groups:

* **Could not transform** — a value whose class is handled but whose content
  did not parse, or for which no offset satisfied the invariants. These are
  the cases the specification calls "flag for manual handling".
* **Needs a human** — free text that is never auto-redacted.
* **Residual** — something recognisably original still present in the output
  after every transform has run. This is the backstop: it does not trust the
  transforms to have been complete.
* **Advisory** — something a reviewer should weigh that no transform can
  fix. These are recorded and reported, and by default they do **not** block:
  nothing identifying has been left in the output, so there is nothing to be
  fail-closed about. A run that wants them treated as blockers sets
  ``PublicationPolicy.advisories_block``.

  Two kinds sit here, and the distinction is worth keeping straight because
  only the first is about fidelity. ``unasserted_relation_changed`` says a
  reading the page never offers has moved. The others —
  ``date_pattern_unchanged``, ``date_offset_below_floor`` and
  ``date_offset_pool_thin`` — are disclosure questions: they say the
  obfuscation on this case is weaker than the policy asks for, which is a
  judgement for a reviewer rather than a defect in the transform. Advisory
  means "no transform can fix this", not "this is harmless".
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class DisclosureCode(str, Enum):
    """Why one element of a page is not publishable as it stands."""

    # --- could not transform ----------------------------------------------
    DATE_UNPARSEABLE = "date_unparseable"
    DATE_TRANSFORM_REJECTED = "date_transform_rejected"
    AGE_UNPARSEABLE = "age_unparseable"
    AGE_TRANSFORM_REJECTED = "age_transform_rejected"
    MEASUREMENT_UNPARSEABLE = "measurement_unparseable"
    MEASUREMENT_TRANSFORM_REJECTED = "measurement_transform_rejected"
    COUNTRY_UNPARSEABLE = "country_unparseable"
    CONTEXT_YEAR_DISAGREEMENT = "context_year_disagreement"
    UNSUPPORTED_VALUE_KIND = "unsupported_value_kind"
    UNSUPPORTED_TEXT_FIELD = "unsupported_text_field"
    SUBSTITUTION_CONFLICT = "substitution_conflict"
    #: A scope naming a real country shows a contextual count small enough to
    #: narrow the population to a handful of people. Not a failed transform —
    #: both values are retained deliberately — but the one combination on the
    #: page that a reviewer has to decide about rather than read. Blocking,
    #: because there is a remedy: mask this example's country, or sign for it.
    RETAINED_COUNTRY_SMALL_COUNT = "retained_country_small_count"

    # --- needs a human ----------------------------------------------------
    HUMAN_TEXT_REVIEW_REQUIRED = "human_text_review_required"
    NARRATIVE_SNIPPET_REVIEW_REQUIRED = "narrative_snippet_review_required"
    CURATOR_TEXT_REVIEW_REQUIRED = "curator_text_review_required"
    #: An ``approve_as_written`` entry whose digest no longer matches the
    #: text. The approval was given for something else, so it does not carry
    #: to what the manifest says now.
    APPROVAL_DIGEST_MISMATCH = "approval_digest_mismatch"

    # --- residual ---------------------------------------------------------
    RESIDUAL_ORIGINAL_VALUE = "residual_original_value"
    RESIDUAL_DATE_TOKEN = "residual_date_token"
    RESIDUAL_DIGIT_RUN = "residual_digit_run"

    # --- advisory ---------------------------------------------------------
    UNASSERTED_RELATION_CHANGED = "unasserted_relation_changed"
    #: Every date in a case came out as reported. Allowed by design, and
    #: reported for exactly that reason: the published page then carries the
    #: pair's real timeline, which is a re-identification question for a
    #: human rather than a defect for the transform to fix.
    DATE_PATTERN_UNCHANGED = "date_pattern_unchanged"
    #: The only offsets that preserved this case's relationships were smaller
    #: than the policy's floor. The dates did move, so the page does not
    #: carry the real timeline, but it carries one close to it.
    DATE_OFFSET_BELOW_FLOOR = "date_offset_below_floor"
    #: Few candidate offsets survived this case's invariants. The accepted
    #: one is sound, but the set it was drawn from is small enough to
    #: enumerate: anyone holding this source and the published page can work
    #: out which offsets the invariants would have accepted, so a case with
    #: three survivors narrows the real timeline to one of three. The salt
    #: does not help here — it chooses *within* the surviving set, and a
    #: reader who can compute the set does not need to know the choice.
    DATE_OFFSET_POOL_THIN = "date_offset_pool_thin"


#: Findings a reviewer must weigh and no transform can fix, rather than
#: something identifying left in the output. See the module docstring: some of
#: these are fidelity notes and some are disclosure judgements.
ADVISORY_CODES: frozenset[DisclosureCode] = frozenset(
    {
        DisclosureCode.UNASSERTED_RELATION_CHANGED,
        DisclosureCode.DATE_PATTERN_UNCHANGED,
        DisclosureCode.DATE_OFFSET_BELOW_FLOOR,
        DisclosureCode.DATE_OFFSET_POOL_THIN,
    }
)


class DisclosureFinding(BaseModel):
    """One element of one case that needs a decision before publication."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: DisclosureCode
    #: The case's *original* example id, so a reviewer can find it in the
    #: internal review. The publication artifacts never carry this; the
    #: findings list is a secure-side document.
    example_id: str
    #: Dotted path to the element, as the substitution walk reports it. This
    #: is the key an :class:`~.policy.ApprovedText` is written against.
    field_path: str = ""
    message: str
    detail: str = ""

    @property
    def is_advisory(self) -> bool:
        return self.code in ADVISORY_CODES

    @property
    def resolution_key(self) -> tuple[str, str, str]:
        return (self.code.value, self.example_id, self.field_path)


class DisclosureReport(BaseModel):
    """Every finding for one publication run, and whether it may proceed."""

    model_config = ConfigDict(extra="forbid")

    #: Blocking, unresolved.
    findings: list[DisclosureFinding] = Field(default_factory=list)
    #: Findings a reviewer has accepted, keyed the same way.
    resolved: list[DisclosureFinding] = Field(default_factory=list)
    #: Reported, not blocking. See the module docstring on why an advisory is
    #: not a disclosure risk.
    advisories: list[DisclosureFinding] = Field(default_factory=list)

    @property
    def blocked(self) -> bool:
        """True while any blocking finding is unresolved.

        Fail-closed: an empty ``findings`` list is the only state that
        publishes without a reviewer's signature, and that state means every
        transform succeeded and the residual scan found nothing.
        """
        return bool(self.findings)

    def by_case(self) -> dict[str, list[DisclosureFinding]]:
        out: dict[str, list[DisclosureFinding]] = {}
        for finding in (*self.findings, *self.resolved, *self.advisories):
            out.setdefault(finding.example_id, []).append(finding)
        return out

    def summary(self) -> str:
        """One line a caller can put in an exception or a log."""
        tail = (
            f"; {len(self.advisories)} advisory note(s)"
            if self.advisories
            else ""
        )
        if not self.findings:
            if self.resolved:
                return (
                    f"{len(self.resolved)} finding(s), all resolved by review"
                    f"{tail}"
                )
            return f"no blocking disclosure findings{tail}"
        counts: dict[str, int] = {}
        for finding in self.findings:
            counts[finding.code.value] = counts.get(finding.code.value, 0) + 1
        parts = ", ".join(f"{code} x{n}" for code, n in sorted(counts.items()))
        return f"{len(self.findings)} unresolved finding(s): {parts}{tail}"


def partition(
    findings: Iterable[DisclosureFinding],
    resolver: object,
    *,
    advisories_block: bool = False,
) -> DisclosureReport:
    """Split findings into blocking, reviewer-accepted and advisory.

    *resolver* is a :class:`~.policy.PublicationApprovals`; it is typed loosely
    here only to keep this module free of a dependency on the policy module,
    which imports nothing from it either.
    """
    unresolved: list[DisclosureFinding] = []
    resolved: list[DisclosureFinding] = []
    advisory: list[DisclosureFinding] = []
    lookup = getattr(resolver, "resolution_for", None)
    for finding in findings:
        accepted = (
            lookup is not None
            and lookup(finding.code.value, finding.example_id, finding.field_path)
            is not None
        )
        if accepted:
            resolved.append(finding)
        elif finding.is_advisory and not advisories_block:
            advisory.append(finding)
        else:
            unresolved.append(finding)
    return DisclosureReport(
        findings=unresolved, resolved=resolved, advisories=advisory
    )


__all__ = [
    "ADVISORY_CODES",
    "DisclosureCode",
    "DisclosureFinding",
    "DisclosureReport",
    "partition",
]
