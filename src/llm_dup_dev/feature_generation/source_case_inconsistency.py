"""Synthesize unified-list Inconsistency rows from a SourceCaseAssessment.

The pair-level deterministic comparator in
:mod:`llm_dup_dev.feature_generation.source_case_comparison` produces a
:class:`SourceCaseAssessment` with one of several verdicts.  Two of
those verdicts translate directly into entries in the unified
:class:`Inconsistency` master list so the weighted inconsistency score
``w_tot`` reflects them the same way AE clinical-picture findings do:

* ``SAME_SOURCE_DIFFERENT_CASE`` — a **decisive** inconsistency (two
  reports share a source identifier but carry different, safely
  comparable case-within-source labels).
* ``SAME_SOURCE_CASE_UNCOMPARABLE`` — a **moderate** inconsistency
  (two reports share a source identifier for a multi-patient source
  but case labels are missing or not safely comparable).  Defaulting
  to moderate ensures that pairs sharing a DOI for a multi-patient
  article land on ``Otherwise_related`` rather than ``Duplicates``
  under the shared-source override path.

All other verdicts yield no synthesized rows — either they carry no
evidence against independence (``SINGLE_PATIENT`` / ``SAME_CASE``) or
they are advisory only (``MULTI_PATIENT_CONTEXT_NO_LINK`` /
``NO_DECISIVE_SIGNAL`` / ``NOT_ASSESSED``).

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
        Zero or one synthesized rows — the source/case component never
        emits more than a single inconsistency per pair.
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
    elif verdict == SourceCaseVerdict.SAME_SOURCE_CASE_UNCOMPARABLE:
        severity = InconsistencySeverity.MODERATE
        description = (
            "Same source identifier but case identifiers are missing "
            "or not safely comparable."
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
