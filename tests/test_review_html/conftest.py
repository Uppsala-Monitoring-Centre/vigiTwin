"""Test fixtures for review_html tests.

Re-exports shared fixtures from the project-level conftest, and holds the
builders the publication-mode tests share — a factory module in the style of
``tests/test_study_eval/conftest.py``. Everything here is synthetic.
"""

from __future__ import annotations

from pydantic import SecretStr

from llm_dup_dev.models.enums import (
    DrugRole,
    InconsistencySeverity,
    ReportingContextLevel,
)
from llm_dup_dev.models.pair_features import (
    ClinicalDateFindingSummary,
    ClinicalDateInconsistencyCounts,
    FeatureContribution,
    Inconsistency,
    InconsistencyCounts,
    ProbabilityEstimate,
    ReportingContext,
)
from llm_dup_dev.models.report import (
    AdverseEvent,
    AgeValue,
    DateValue,
    Drug,
    MeasurementValue,
    PartialDate,
    Report,
    SourcedValue,
)
from llm_dup_dev.review_html.evidence.models import (
    EvidenceCaseVM,
    EvidencePageVM,
    number_cases,
)
from llm_dup_dev.review_html.evidence.settings import EvidenceRunSettings
from llm_dup_dev.review_html.evidence.publication import (
    PublicationApprovals,
    PublicationPolicy,
    PublicationSecrets,
    ScrambleResult,
    scramble_page,
)
from tests.conftest import report_a, report_b, report_c, report_empty
from llm_dup_dev.review_html.evidence.build import build_evidence_case
from tests.test_review_html.test_evidence_build import (
    _case,
    _classification,
    _features,
)

__all__ = [
    "RUN_SETTINGS",
    "masking_policy",
    "publication_page",
    "report_a",
    "report_b",
    "report_c",
    "report_empty",
    "scramble",
    "secrets",
    "worked_example_case",
]

#: A fixed salt, so every publication test is reproducible. Never a default
#: in the library: see ``PublicationSecrets.from_env``.
_TEST_SALT = "publication-test-salt"

#: The decision settings a saved run records. Supplied to the worked examples
#: so their pages quote the thresholds the run used — without them the page
#: correctly declines to name a threshold, which is right and makes a poor
#: illustration.
RUN_SETTINGS = EvidenceRunSettings(
    lmatch_compelling=3.0,
    lmatch_intermediate=2.0,
    lmatch_limited=1.0,
    epsilon=1e-15,
    source="run_manifest.yaml",
)


def secrets(salt: str = _TEST_SALT) -> PublicationSecrets:
    return PublicationSecrets(salt=SecretStr(salt))


def _detailed_report(
    report_id: str,
    *,
    age: float,
    onset: str,
    report_date: str,
    drug_start_year: str = "2023",
    weight: float | None = None,
    weight_decimals: int = 2,
    country: str = "SE",
) -> Report:
    """One report of the worked example from the specification.

    Carries a date at each of the three precisions, so a single offset has to
    cope with all of them at once: a day-precision onset and report date, and
    a year-only drug start.
    """
    return Report(
        report_id=report_id,
        patient_sex_values=[SourcedValue(value="female", sources=["structured"])],
        patient_age_values=[
            AgeValue(value=age, unit="years", sources=["structured"])
        ],
        patient_weight_values=(
            []
            if weight is None
            else [
                MeasurementValue(
                    value=weight, unit="kg", sources=["structured"]
                )
            ]
        ),
        country_of_origin_values=[
            SourcedValue(value=country, sources=["structured"])
        ],
        report_date_values=[
            DateValue(
                value=report_date, resolution="day", sources=["structured"]
            )
        ],
        drugs=[
            Drug(
                substance="levonorgestrel",
                sources=["structured"],
                role=DrugRole.SUSPECTED,
                start_date=PartialDate(value=drug_start_year, resolution="year"),
            )
        ],
        adverse_events=[
            AdverseEvent(
                pt_name="device expulsion",
                sources=["structured"],
                start_date=PartialDate(value=onset, resolution="day"),
            )
        ],
    )


def worked_example_case(
    *,
    weight: float | None = None,
    clinical_date_finding: bool = False,
    report_ids: tuple[str, str] = ("R1", "R2"),
) -> EvidenceCaseVM:
    """The illustrative example: ages 81, onsets a day apart, a year-only start.

    ==========================  ==============
    Age, both reports           81 years
    Adverse event onset, A      2023-11-13
    Adverse event onset, B      2023-11-12
    Drug start, both            2023
    Report date, both           2024-02-20
    ==========================  ==============

    With ``clinical_date_finding``, the pair also carries a finding that
    states a separation between the year-only drug start and a report date —
    which turns an advisory into a hard invariant and makes most offsets
    unacceptable.
    """
    first, second = report_ids
    r1 = _detailed_report(
        first,
        age=81,
        onset="2023-11-13",
        report_date="2024-02-20",
        weight=weight,
    )
    r2 = _detailed_report(
        second,
        age=81,
        onset="2023-11-12",
        report_date="2024-02-20",
        weight=weight,
    )
    kwargs = {
        "pair_id": f"{first}__{second}",
        "matching_drugs": ["levonorgestrel"],
        "matching_AEs": ["device expulsion"],
        "reporting_context": ReportingContext(
            level=ReportingContextLevel.COUNTRY_YEAR, country="SE", year=2024
        ),
        "probability": ProbabilityEstimate(
            p_joint=1e-7,
            p_match=1e-7,
            feature_contributions=[
                FeatureContribution(
                    feature="patient_sex", p_f=0.5, method="domain_size"
                )
            ],
        ),
    }
    if clinical_date_finding:
        kwargs["clinical_date_findings"] = [
            ClinicalDateFindingSummary(
                entity_label="drug_start:levonorgestrel",
                severity=InconsistencySeverity.MODERATE,
                source_date="2023",
                matched_date="2024-02-20",
                diff_days=51,
            )
        ]
        kwargs["inconsistencies"] = [
            Inconsistency(
                feature="clinical_date",
                severity=InconsistencySeverity.MODERATE,
                description="Drug start date for levonorgestrel",
            )
        ]
        kwargs["inconsistency_counts"] = InconsistencyCounts(moderate=1)
    return build_evidence_case(
        report_1=r1,
        report_2=r2,
        pair_id=f"{first}__{second}",
        features=_features(**kwargs),
        classification=_classification(pair_id=f"{first}__{second}"),
    )


def publication_page(*cases: EvidenceCaseVM, title: str = "Evidence") -> EvidencePageVM:
    """A page of cases, numbered the way the writers number them."""
    return EvidencePageVM(
        title=title, generated_at="2026-01-01T00:00:00Z", cases=number_cases(list(cases))
    )


def masking_policy(**kw) -> PublicationPolicy:
    """A policy that masks countries, which is no longer the default."""
    return PublicationPolicy(mask_countries=True, **kw)


def scramble(
    page: EvidencePageVM,
    *,
    policy: PublicationPolicy | None = None,
    approvals: PublicationApprovals | None = None,
    salt: str = _TEST_SALT,
) -> ScrambleResult:
    return scramble_page(
        page, policy=policy, secrets=secrets(salt), approvals=approvals
    )


def case_with_human_text() -> EvidenceCaseVM:
    """A case carrying the two kinds of text no transform may touch.

    A curator's written interpretation, and the verbatim narrative snippets
    behind a narrative-derived demographic value.
    """
    from llm_dup_dev.models.narrative_demographics import (
        NarrativeAgeValue,
        NarrativeDemographics,
    )
    from llm_dup_dev.review_html.evidence.build import ExamplePresentation

    r1 = _detailed_report(
        "R1", age=81, onset="2023-11-13", report_date="2024-02-20"
    )
    r2 = _detailed_report(
        "R2", age=81, onset="2023-11-12", report_date="2024-02-20"
    )
    narrative = NarrativeDemographics(
        ages=[
            NarrativeAgeValue(
                value=81,
                unit="years",
                evidence="An 81-year-old woman in Uppsala presented in March.",
            )
        ]
    )
    return _case(
        r1,
        r2,
        _features(
            matching_drugs=["levonorgestrel"],
            reporting_context=ReportingContext(
                level=ReportingContextLevel.COUNTRY_YEAR,
                country="SE",
                year=2024,
            ),
        ),
        narrative_demographics_1=narrative,
        narrative_demographics_2=narrative,
        presentation=ExamplePresentation(
            example_id="se-twins",
            example_label="The Uppsala pair",
            title="Two reports of the same device expulsion",
            lede="Both reports reached the centre within a week.",
            kicker="Worked example",
            case_interpretation=(
                "The reporting clinician in Uppsala confirmed by telephone "
                "that these are one patient."
            ),
            summary_mode="both",
        ),
    )


__all__.append("case_with_human_text")


def shared_source_case(
    *,
    report_ids: tuple[str, str] = ("R1", "R2"),
    source_identifier: str = "SYNTH-TRIAL-0001",
) -> EvidenceCaseVM:
    """A pair settled by a definitive shared-source verdict.

    Both narratives name one synthetic trial registration, and the case
    labels *within* that trial differ — so the source context establishes
    shared provenance and distinct cases in one step, and the relationship is
    pinned to ``Different_case`` whatever the inconsistency penalty comes to.

    The point of the fixture is that the page has to say so: a matching score
    and a penalty are both computed here and neither decided anything.
    """
    from llm_dup_dev.models.classification import (
        ClassificationResult,
        InconsistencyPenalty,
        PenaltyTerm,
    )
    from llm_dup_dev.models.enums import (
        IndependenceAssessment,
        IndependenceReason,
        InconsistencySeverity,
        RelationshipAssessment,
    )
    from llm_dup_dev.models.narrative_source_case import (
        SourceCaseAssessment,
        SourceCaseVerdict,
        SourceIdentifierType,
    )
    from llm_dup_dev.feature_generation.source_case_inconsistency import (
        source_case_findings,
    )

    first, second = report_ids
    r1 = _detailed_report(
        first, age=81, onset="2023-11-13", report_date="2024-02-20"
    )
    r2 = _detailed_report(
        second, age=74, onset="2023-11-12", report_date="2024-02-20"
    )
    assessment = SourceCaseAssessment(
        verdict=SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE,
        shared_source_identifier=source_identifier,
        shared_source_identifier_type=SourceIdentifierType.TRIAL_REGISTRATION,
        case_identifiers_compared=True,
        case_identifiers_comparable=True,
        reasoning="Synthetic fixture: same registration, different case labels.",
    )
    findings = source_case_findings(assessment)
    features = _features(
        pair_id=f"{first}__{second}",
        matching_drugs=["levonorgestrel"],
        matching_AEs=["device expulsion"],
        reporting_context=ReportingContext(
            level=ReportingContextLevel.COUNTRY_YEAR, country="SE", year=2024
        ),
        probability=ProbabilityEstimate(
            p_joint=1e-7,
            p_match=1e-7,
            feature_contributions=[
                FeatureContribution(
                    feature="patient_sex", p_f=0.5, method="domain_size"
                )
            ],
        ),
        L_match=4.2,
        source_case_assessment=assessment,
        inconsistencies=findings,
        inconsistency_counts=InconsistencyCounts(decisive=len(findings)),
    )
    return build_evidence_case(
        report_1=r1,
        report_2=r2,
        pair_id=f"{first}__{second}",
        features=features,
        run_settings=RUN_SETTINGS,
        classification=ClassificationResult(
            pair_id=f"{first}__{second}",
            independence_assessment=IndependenceAssessment.NON_INDEPENDENT,
            independence_reason=IndependenceReason.SHARED_SOURCE,
            relationship_assessment=RelationshipAssessment.DIFFERENT_CASE,
            # No threshold: the verdict pinned the relationship, so the
            # penalty was never put to a comparison. The weight is the one a
            # run gives a decisive finding, so the fixture carries the
            # arithmetic a real pinned pair carries.
            penalty=InconsistencyPenalty(
                w_tot=100.0,
                threshold=None,
                terms=[
                    PenaltyTerm(
                        severity=InconsistencySeverity.DECISIVE,
                        count=1,
                        weight=100.0,
                    )
                ],
            ),
        ),
    )


def probabilistic_case(
    *, report_ids: tuple[str, str] = ("R5", "R6")
) -> EvidenceCaseVM:
    """A pair settled the other way: rarity, then a weighed penalty.

    The counterpart to :func:`shared_source_case`. No source context at all,
    so both stages run on the figures and the page should say so in the same
    two tiles, in the same place.
    """
    from llm_dup_dev.models.classification import (
        ClassificationResult,
        InconsistencyPenalty,
        PenaltyTerm,
    )
    from llm_dup_dev.models.enums import (
        IndependenceAssessment,
        IndependenceReason,
        InconsistencySeverity,
        RelationshipAssessment,
    )

    first, second = report_ids
    r1 = _detailed_report(
        first, age=54, onset="2023-11-13", report_date="2024-02-20"
    )
    r2 = _detailed_report(
        second, age=54, onset="2023-11-12", report_date="2024-02-20"
    )
    features = _features(
        pair_id=f"{first}__{second}",
        matching_drugs=["levonorgestrel"],
        matching_AEs=["device expulsion"],
        reporting_context=ReportingContext(
            level=ReportingContextLevel.COUNTRY_YEAR, country="SE", year=2024
        ),
        probability=ProbabilityEstimate(
            p_joint=1e-8,
            p_match=1e-8,
            feature_contributions=[
                FeatureContribution(
                    feature="patient_sex", p_f=0.5, method="domain_size"
                ),
                FeatureContribution(
                    feature="patient_age", p_f=0.02, method="domain_size"
                ),
            ],
        ),
        L_match=5.1,
        clinical_date_findings=[
            ClinicalDateFindingSummary(
                entity_label="ae_onset:device expulsion",
                severity=InconsistencySeverity.MINOR,
                source_date="2023-11-13",
                matched_date="2023-11-12",
                diff_days=1,
            )
        ],
        clinical_date_inconsistency_counts=(
            ClinicalDateInconsistencyCounts(n_minor=1)
        ),
    )
    return build_evidence_case(
        report_1=r1,
        report_2=r2,
        pair_id=f"{first}__{second}",
        features=features,
        run_settings=RUN_SETTINGS,
        classification=ClassificationResult(
            pair_id=f"{first}__{second}",
            independence_assessment=IndependenceAssessment.NON_INDEPENDENT,
            independence_reason=IndependenceReason.RARITY_COMPELLING,
            relationship_assessment=RelationshipAssessment.SAME_CASE,
            penalty=InconsistencyPenalty(
                w_tot=0.4,
                threshold=4.0,
                terms=[
                    PenaltyTerm(
                        severity=InconsistencySeverity.MINOR,
                        count=1,
                        weight=0.4,
                    )
                ],
            ),
        ),
    )


__all__ += ["probabilistic_case", "shared_source_case"]
