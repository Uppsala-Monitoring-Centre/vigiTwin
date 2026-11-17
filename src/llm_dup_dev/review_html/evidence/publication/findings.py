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
* **Advisory** — something a reviewer should weigh that is not a defect: a
  relationship that moved which the page never asserts, or a case whose dates
  all came out as reported. These are recorded and reported, and by default they do **not**
  block: nothing has leaked, so there is no disclosure risk to be
  fail-closed about. What has changed is a reading the page does not offer,
  which is a question of evidential fidelity rather than confidentiality. A
  run that wants them treated as blockers sets
  ``PublicationPolicy.advisories_block``.
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

    # --- needs a human ----------------------------------------------------
    HUMAN_TEXT_REVIEW_REQUIRED = "human_text_review_required"
    NARRATIVE_SNIPPET_REVIEW_REQUIRED = "narrative_snippet_review_required"
    CURATOR_TEXT_REVIEW_REQUIRED = "curator_text_review_required"

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


#: Findings that record a relationship the page never stated, rather than
#: something identifying left in the output.
ADVISORY_CODES: frozenset[DisclosureCode] = frozenset(
    {
        DisclosureCode.UNASSERTED_RELATION_CHANGED,
        DisclosureCode.DATE_PATTERN_UNCHANGED,
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
