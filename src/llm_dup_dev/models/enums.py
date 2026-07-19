"""Enumeration types for the duplicate adjudication pipeline.

Defines the controlled vocabularies used throughout the pipeline
for classification outcomes, severity levels, and context types.

The two adjudication assessments vs. user-facing labels
-------------------------------------------------------
The pipeline answers two sequential questions about a report pair:

* an **independence assessment** (``IndependenceAssessment``) — did the
  two reports arise independently? Independence is rejected either on
  direct evidence of shared provenance (a shared source identifier) or
  when the rarity-based matching evidence makes independent origin too
  unlikely (the matched features are too rare to have coincided if the
  reports were independent draws from the comparison population);
* for non-independent pairs, a **relationship assessment**
  (``RelationshipAssessment``) — do the reports describe the same
  underlying case (``Same_case``), demonstrably different cases
  (``Different_case``), or is the evidence insufficient to safely
  resolve the question (``Abstain``)?

These two are combined by ``ClassificationResult.final_label`` into the
user-facing labels ``Duplicates`` / ``Otherwise_related`` / ``Unrelated``
/ ``Abstain``. Downstream consumers (eval framework, review HTML, secure
repo) should depend on ``final_label`` rather than on the raw internal
enums.

The "independence assessment" framing is deliberate: a low collision
probability is evidence *against independence* but not specifically
*for same individual* — same reporter, same study, copies of the same
report through different channels, or unusual shared context can all
produce a small expected-collision count without the two reports
describing the same patient. The same-case / different-case distinction
is therefore a separate, second assessment.
"""

from enum import Enum


class NarrativeConsistency(str, Enum):
    """Narrative consistency categories from LLM assessment.

    See: docs/duplicate_adjudication_spec.md § Narrative-derived features

    Values
    ------
    STRONGLY_CONSISTENT : narratives clearly describe the same patient
    CONSISTENT : probable but not certain match
    NEUTRAL : narratives too sparse to judge
    INCONSISTENT : narratives clearly describe different patients
    NOT_AVAILABLE : LLM assessment was not performed or failed
    """

    STRONGLY_CONSISTENT = "strongly_consistent"
    CONSISTENT = "consistent"
    NEUTRAL = "neutral"
    INCONSISTENT = "inconsistent"
    NOT_AVAILABLE = "not_available"


class InconsistencySeverity(str, Enum):
    """Severity levels for detected inconsistencies between reports.

    See: docs/duplicate_adjudication_spec.md § Severity Levels

    Levels:
        minor     – small difference, weakly suggests different individuals
        moderate  – meaningful difference, weakens the duplicate hypothesis
        strong    – strongly suggests different individuals
        decisive  – forces Different classification (e.g. distinct
                    patient IDs from the same study in the narratives)
    """

    MINOR = "minor"
    MODERATE = "moderate"
    STRONG = "strong"
    DECISIVE = "decisive"


class AEClinicalPictureStatus(str, Enum):
    """Assessment status for the AE clinical-picture inconsistency component.

    Distinguishes "the LLM assessed the AE pictures and produced findings"
    from "no LLM assessment was performed" (no LLM client configured,
    trivially-matched skip, or LLM error).  Downstream code must treat
    ``NOT_ASSESSED`` as *no information*, not as "assessed and found no
    discrepancies".

    The accompanying ``reason`` string (carried alongside the status on
    ``PairFeatures.ae_clinical_picture_reason``) refines ``NOT_ASSESSED``
    into one of: ``""`` (assessed), ``"no_llm_client"``,
    ``"trivially_matched"``, or ``"llm_error"``.  Reason is a string
    rather than an enum so new codes can be added without enum churn.

    See: docs/duplicate_adjudication_spec.md § AE clinical-picture inconsistency
    """

    ASSESSED = "assessed"
    NOT_ASSESSED = "not_assessed"


class DrugRole(str, Enum):
    """Role of a drug in a pharmacovigilance report.

    Used by the drug-set inconsistency check to distinguish clinically
    central drugs (suspected, interacting) from background medication.

    See: docs/data_contracts.md § Drug Information
    """

    SUSPECTED = "suspected"
    CONCOMITANT = "concomitant"
    INTERACTING = "interacting"


class ReportingContextLevel(str, Enum):
    """Granularity of the reporting context used for probability estimation.

    See: docs/duplicate_adjudication_spec.md § Reporting Context
    """

    COUNTRY_YEAR = "country_year"
    COUNTRY = "country"
    GLOBAL = "global"


class AnchorType(str, Enum):
    """Type of anchor population selected for probability estimation.

    See: docs/duplicate_adjudication_spec.md § Anchor Selection
    """

    DRUGS = "drugs"
    AES = "AEs"
    DRUGS_AES = "drugs_AEs"


class IndependenceAssessment(str, Enum):
    """Internal independence assessment produced by the decision logic.

    Captures whether the pair's independence can be rejected: either
    directly, via a reliable shared source identifier (shared
    provenance), or via the rarity-based matching evidence — a
    ``NON_INDEPENDENT`` verdict then means the matched features are too
    unlikely to have arisen by chance if the reports were independent
    draws from the comparison population. The same-case vs
    different-case distinction is made separately by the
    ``RelationshipAssessment``.

    The user-facing label is derived from this enum (plus
    ``RelationshipAssessment``) by ``ClassificationResult.final_label``;
    downstream consumers should depend on the final label rather than
    on this raw enum.

    See: docs/duplicate_adjudication_spec.md § Independence assessment
    """

    NON_INDEPENDENT = "Non-independent"
    INDEPENDENT = "Independent"


class IndependenceReason(str, Enum):
    """Audit trail for which branch in the decision logic fired.

    Attached to :class:`ClassificationResult` so downstream review can
    tell *why* a pair landed on its independence assessment. The
    ``RARITY_*`` values correspond to the four L_match zones of the
    rarity-based matching evidence; ``SHARED_SOURCE`` means independence
    was rejected on direct evidence of shared provenance (a reliable
    shared source identifier derived from the narratives, or a
    shared-source verdict from the source-case comparison).

    See: docs/duplicate_adjudication_spec.md § Independence assessment
    """

    RARITY_COMPELLING = "rarity_compelling"
    RARITY_INTERMEDIATE = "rarity_intermediate"
    RARITY_LIMITED = "rarity_limited"
    RARITY_INSUFFICIENT = "rarity_insufficient"
    SHARED_SOURCE = "shared_source"


class RelationshipAssessment(str, Enum):
    """Relationship assessment for pairs whose independence was rejected.

    Answers the second adjudication question: do the two reports
    describe the same underlying case (``SAME_CASE``), or is the pair
    linked by something other than being the same individual case —
    e.g. same reporter, same study, two patients on the same unusual
    regimen (``DIFFERENT_CASE``)? The decision logic chooses based on
    the inconsistency signals (weighted score ``w_tot``) interpreted
    against the strength of the matching evidence and the source
    context.

    ``ABSTAIN`` is emitted for epistemically underdetermined
    same-source scenarios: non-independence is established (same source
    identifier confirmed) but the available evidence is insufficient to
    safely determine whether the reports describe the same individual.

    ``ClassificationResult.final_label`` maps these to the user-facing
    labels: ``Same_case`` → ``Duplicates``, ``Different_case`` →
    ``Otherwise_related``, ``Abstain`` → ``Abstain``.

    See: docs/duplicate_adjudication_spec.md § Relationship assessment
    """

    SAME_CASE = "Same_case"
    DIFFERENT_CASE = "Different_case"
    ABSTAIN = "Abstain"
