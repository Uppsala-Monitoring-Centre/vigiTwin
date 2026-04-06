"""Enumeration types for the same-individual assessment pipeline.

Defines the controlled vocabularies used throughout the pipeline
for classification outcomes, severity levels, and context types.
"""

from enum import Enum


class NarrativeConsistency(str, Enum):
    """Narrative consistency categories from LLM assessment.

    See: docs/same_individual_assessment_spec.md § Narrative Consistency Assessment

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

    See: docs/same_individual_assessment_spec.md § Severity Levels

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

    See: docs/same_individual_assessment_spec.md § Reporting Context
    """

    COUNTRY_YEAR = "country_year"
    COUNTRY = "country"
    GLOBAL = "global"


class AnchorType(str, Enum):
    """Type of anchor population selected for probability estimation.

    See: docs/same_individual_assessment_spec.md § Anchor Selection
    """

    DRUGS = "drugs"
    AES = "AEs"
    DRUGS_AES = "drugs_AEs"


class PrimaryClassification(str, Enum):
    """Primary classification outcome for a report pair.

    See: docs/same_individual_assessment_spec.md § Decision logic
    """

    SAME_INDIVIDUAL = "Same individual"
    DIFFERENT_INDIVIDUALS = "Different individuals"
    ABSTAIN = "Abstain"


class RelationshipClassification(str, Enum):
    """Sub-classification when primary is 'Different individuals'.

    Determines whether non-duplicate reports are clinically related.
    See: docs/same_individual_assessment_spec.md § Decision logic
    """

    RELATED = "Related"
    UNRELATED = "Unrelated"
    ABSTAIN = "Abstain"
