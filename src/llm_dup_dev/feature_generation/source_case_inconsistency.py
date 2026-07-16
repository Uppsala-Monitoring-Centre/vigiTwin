"""Synthesize unified-list Inconsistency rows from a SourceCaseAssessment.

The pair-level deterministic comparator in
:mod:`llm_dup_dev.feature_generation.source_case_comparison` produces a
:class:`SourceCaseAssessment` with one of several verdicts.  Only one
of those verdicts translates into an entry in the unified
:class:`Inconsistency` master list:

* ``SAME_SOURCE_DIFFERENT_CASE`` — a **decisive** inconsistency (two
  reports share a source identifier but carry different, safely
  comparable case-within-source labels).

All other verdicts yield no synthesized rows.  In particular,
``SAME_SOURCE_UNCOMPARABLE_CASE`` represents *absence of comparable
evidence* (a shared source identifier without safely comparable case
labels), not a discrepancy — synthesizing a moderate inconsistency
for it would mislabel "we cannot tell" as "we found a moderate
mismatch" and inflate ``w_tot`` accordingly.

That distinction is load-bearing rather than cosmetic.  The
shared-source override in :mod:`llm_dup_dev.classifier.decision_logic`
routes this verdict to a three-way rule whose first branch is
``w_tot >= w_tot_limited_zone`` → ``Otherwise_related``.  A synthesized
moderate row (``w_moderate`` = 1.0, meeting the default bar of 1.0)
would therefore fire that branch on its own, foreclosing both the
``Duplicates`` and ``Abstain`` outcomes and denying the pair any
adjudication on its patient-specific evidence.  Emitting nothing keeps
``w_tot`` reflecting only non-source-case signals, which is what makes
the three-way rule's comparison apples-to-apples.

The remaining verdicts likewise emit nothing — ``SINGLE_PATIENT`` /
``SAME_CASE`` carry no evidence against independence, and
``MULTI_PATIENT_CONTEXT_NO_LINK`` / ``NO_DECISIVE_SIGNAL`` /
``NOT_ASSESSED`` are advisory only.

See: docs/duplicate_adjudication_spec.md § Decision logic
"""

from __future__ import annotations

from llm_dup_dev.models.enums import InconsistencySeverity
from llm_dup_dev.models.narrative_source_case import (
    SourceCaseAssessment,
    SourceCaseVerdict,
)
from llm_dup_dev.models.pair_features import Inconsistency


_SOURCE = "narrative_source_case"


def source_case_findings(
    assessment: SourceCaseAssessment | None,
) -> list[Inconsistency]:
    """Map a :class:`SourceCaseAssessment` to unified ``Inconsistency`` rows.

    Parameters
    ----------
    assessment : SourceCaseAssessment or None
        The pair-level verdict from ``compare_source_case``.  ``None``
        (no extraction/comparison performed) yields no findings.

    Returns
    -------
    list[Inconsistency]
        Zero or one synthesized rows.  A row is emitted only for
        ``SAME_SOURCE_DIFFERENT_CASE``; every other verdict yields the
        empty list.
    """
    if assessment is None:
        return []

    verdict = assessment.verdict

    if verdict == SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE:
        severity = InconsistencySeverity.DECISIVE
        description = (
            "Same source identifier but different comparable "
            "case-within-source identifiers."
        )
    else:
        return []

    rationale_parts = [verdict.value]
    if assessment.shared_source_identifier:
        ident_type = (
            assessment.shared_source_identifier_type.value
            if assessment.shared_source_identifier_type is not None
            else "source_identifier"
        )
        rationale_parts.append(
            f"{ident_type}={assessment.shared_source_identifier}"
        )
    rationale = "; ".join(rationale_parts)

    return [
        Inconsistency(
            feature=f"source_case_{verdict.value}",
            severity=severity,
            description=description,
            source=_SOURCE,
            kind=verdict.value,
            rationale=rationale,
        )
    ]
