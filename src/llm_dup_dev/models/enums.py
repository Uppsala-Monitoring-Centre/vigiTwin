"""Enumeration types for the duplicate adjudication pipeline.

Defines the controlled vocabularies used throughout the pipeline
for classification outcomes, severity levels, and context types.

Internal independence assessment vs. user-facing labels
-------------------------------------------------------
The pipeline produces two layered outputs:

* an **independence assessment** (``IndependenceAssessment``) that
  captures whether the rarity-based matching evidence is strong enough
  to reject the null hypothesis of independence between the two
  reports — i.e. whether the matched features are too unlikely to
  have arisen if the reports were independent draws from the
  comparison population;
* when the independence null is rejected, a **duplicate relationship**
  sub-label (``DuplicateRelationship``) that uses the inconsistency
  signals to distinguish a likely-duplicate case (``Duplicates``) from
  one where the reports are linked by something other than being the
  same individual case (``Otherwise_related``).

These two are combined by ``ClassificationResult.final_label`` into the
user-facing labels ``Duplicates`` / ``Otherwise_related`` / ``Unrelated``.
Downstream consumers (eval framework, review HTML, secure repo) should
depend on ``final_label`` rather than on the raw internal enums.

The "independence assessment" framing replaces an earlier
"same-individual assessment" framing. The shift is deliberate: a low
collision probability is evidence *against independence* but not
specifically *for same individual* — same reporter, same study, copies
of the same report through different channels, or unusual shared
context can all produce a small expected-collision count without the
two reports describing the same patient. Calling the intermediate
assessment "independence" makes that explicit, and the duplicate /
otherwise-related distinction is then handled by the inconsistency
component.
"""

from enum import Enum


class NarrativeConsistency(str, Enum):
    """Narrative consistency categories from LLM assessment.

    See: docs/duplicate_adjudication_spec.md § Narrative Consistency Assessment

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

    Captures whether the rarity-based matching evidence is strong
    enough to reject the null hypothesis that the two reports are
    independent draws from the comparison population. A
    ``NON_INDEPENDENT`` verdict means the matched features are too
    unlikely to have arisen by chance under that null; the duplicate
    vs otherwise-related distinction is then made by the
    ``DuplicateRelationship`` sub-label using inconsistency signals.

    The user-facing label is derived from this enum (plus
    ``DuplicateRelationship``) by ``ClassificationResult.final_label``;
    downstream consumers should depend on the final label rather than
    on this raw enum.

    The rarity-zone path assigns only ``NON_INDEPENDENT`` or
    ``INDEPENDENT``. The shared-source override path additionally
    assigns ``NON_INDEPENDENT`` paired with
    ``DuplicateRelationship.ABSTAIN`` for epistemically underdetermined
    same-source scenarios. ``IndependenceAssessment.ABSTAIN`` itself is
    kept as a defensive backstop but is not emitted by standard paths.

    See: docs/duplicate_adjudication_spec.md § Decision logic
    """

    NON_INDEPENDENT = "Non-independent"
    INDEPENDENT = "Independent"
    ABSTAIN = "Abstain"


class IndependenceReason(str, Enum):
    """Audit trail for which branch in the decision logic fired.

    Attached to :class:`ClassificationResult` so downstream review can
    tell *why* a pair landed on its independence assessment. The
    ``RARITY_*`` values correspond to the four L_match zones of the
    rarity-based branch; ``SHARED_SOURCE`` is set by the shared-source
    override path (when a reliable shared source identifier has been
    derived from the narratives) and by the multi-patient three-way
    rule that handles ``MULTI_PATIENT_CONTEXT_NO_LINK`` pairs with
    ``L_match > lmatch_weak``. ``ABSTAIN`` is a defensive backstop for
    paths not covered by the above; it is not emitted by any standard
    code path.

    See: docs/duplicate_adjudication_spec.md § Decision logic
    """

    RARITY_STRONG = "rarity_strong"
    RARITY_MEDIUM = "rarity_medium"
    RARITY_WEAK = "rarity_weak"
    RARITY_INSUFFICIENT = "rarity_insufficient"
    SHARED_SOURCE = "shared_source"
    ABSTAIN = "abstain"


class DuplicateRelationship(str, Enum):
    """Sub-classification used when independence has been rejected.

    Distinguishes a likely-duplicate report pair from one where the
    pair is linked by something other than being the same individual
    case (e.g. same reporter, same study, two patients on the same
    unusual regimen). The decision logic chooses between these based
    on the weighted inconsistency score ``w_tot``.

    Mirrors the user-facing label vocabulary (``Duplicates`` /
    ``Otherwise_related`` / ``Abstain``).

    ``ABSTAIN`` is emitted by the shared-source override path for
    epistemically underdetermined same-source scenarios: non-independence
    is established (same source identifier confirmed) but the available
    evidence is insufficient to safely determine whether the reports
    describe the same individual. It maps to the user-facing label
    ``Abstain`` via ``ClassificationResult.final_label``.

    See: docs/duplicate_adjudication_spec.md § Decision logic
    """

    DUPLICATES = "Duplicates"
    OTHERWISE_RELATED = "Otherwise_related"
    ABSTAIN = "Abstain"
