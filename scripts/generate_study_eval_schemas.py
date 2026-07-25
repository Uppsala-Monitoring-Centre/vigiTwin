"""Generate committed schema + example JSON for the study_eval models.

Writes schemas/study_*.schema.json and examples/study_*_example.json. Run from
the repo root. Idempotent.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from llm_dup_dev.study_eval.manifest import ArtifactRef, EvaluationRunManifest
from llm_dup_dev.study_eval.records import SamplingRecord
from llm_dup_dev.study_eval.sampling import SCLECase, SCLESample
from llm_dup_dev.study_eval.schemas import (
    PairPrediction,
    PredictionSet,
    ReferencePair,
    ReferenceSet,
)
from llm_dup_dev.study_eval.scle.agreement import AgreementReport, DimensionAgreement
from llm_dup_dev.study_eval.scle.arbitration import (
    ArbitrationDecision,
    ArbitrationSubmission,
)
from llm_dup_dev.study_eval.scle.consensus import (
    ConsensusAssessment,
    SCLEConsensusResult,
)
from llm_dup_dev.study_eval.scle.discrepancy import (
    DimensionDisagreement,
    DiscrepancyReport,
    PairDiscrepancy,
)
from llm_dup_dev.study_eval.scle.form import (
    CaseDifficulty,
    PrimaryErrorSource,
    ReferenceLabelIssue,
    ReviewerSubmission,
    SCLEAutoContext,
    SCLECaseAssessment,
    YesNoUnclear,
)

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "schemas"
EXAMPLES = ROOT / "examples"

NOW = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)


def _reference_set():
    return ReferenceSet(
        set_id="precision_primary",
        role="precision",
        label="Screening candidate set",
        provenance="model_v1_candidates",
        pairs=[
            ReferencePair(
                pair_id="R001__R002",
                report_id1="R001",
                report_id2="R002",
                cluster_id="C1",
                reference_label="duplicate",
                annotator_id="ref-annotator",
                annotated_at="2026-03-15",
            ),
            ReferencePair(
                pair_id="R003__R004",
                report_id1="R003",
                report_id2="R004",
                cluster_id="C2",
                reference_label="non_duplicate",
            ),
        ],
    )


def _prediction_set():
    return PredictionSet(
        configuration_id="primary",
        predictions={
            "R001__R002": PairPrediction(
                pair_id="R001__R002", adjudicator_label="Duplicates", screening_retrieved=True
            ),
            "R003__R004": PairPrediction(
                pair_id="R003__R004", adjudicator_label="Unrelated", screening_retrieved=True
            ),
        },
    )


def _sampling_record():
    return SamplingRecord(
        procedure_id="scle_precision_v1:TP",
        seed="study-2026",
        created_at=NOW,
        package_version="0.1.0",
        python_version="3.11.8",
        per_stratum_target=10,
        stratum_sizes={"TP": 22},
        selected_pair_ids=["R001__R002"],
        backup_pair_ids={"TP": ["R005__R006", "R007__R008"]},
    )


def _auto_context(pair_id, cell, mis, disagree):
    return SCLEAutoContext(
        pair_id=pair_id,
        evaluation_component="precision",
        reference_label="duplicate",
        adjudicator_final_label="Duplicates" if not mis else "Otherwise_related",
        screening_retrieved=True,
        confusion_cell=cell,
        methods_disagree=disagree,
        is_misclassified=mis,
        decision_trace_summary="final_label=Duplicates; independence=Non-independent",
        packet_filename=f"{pair_id}.html",
    )


def _scle_sample():
    return SCLESample(
        evaluation_component="precision",
        cases=[
            SCLECase(
                pair_id="R001__R002",
                evaluation_component="precision",
                stratum="TP",
                auto_context=_auto_context("R001__R002", "TP", False, False),
            ),
            SCLECase(
                pair_id="R003__R004",
                evaluation_component="precision",
                stratum="FN",
                auto_context=_auto_context("R003__R004", "FN", True, True),
            ),
        ],
        backups=[
            SCLECase(
                pair_id="R005__R006",
                evaluation_component="precision",
                stratum="TP",
                backup_rank=1,
                auto_context=_auto_context("R005__R006", "TP", False, False),
            ),
        ],
        records=[_sampling_record()],
    )


def _assessment(pair_id, mis):
    return SCLECaseAssessment(
        pair_id=pair_id,
        reviewer_id="alice",
        free_text_assessment="Decisive shared source identifier.",
        case_difficulty=CaseDifficulty.NON_TRIVIAL,
        reference_label_issue=ReferenceLabelIssue.NO_APPARENT_ISSUE,
        never_event=YesNoUnclear.NO if mis else None,
        unexpected_method_behaviour=YesNoUnclear.NO if mis else None,
        material_input_data_limitation=YesNoUnclear.NO if mis else None,
        primary_error_source=PrimaryErrorSource.RELATIONSHIP_ASSESSMENT if mis else None,
        implicated_feature_domains=["narrative"] if mis else [],
        method_difference_explanation="Adjudicator down-weighted rarity." if mis else None,
    )


def _reviewer_submission(reviewer):
    return ReviewerSubmission(
        workbook_id="wb-" + reviewer,
        reviewer_id=reviewer,
        evaluation_component="precision",
        sample_checksum="0" * 64,
        assessments=[_assessment("R001__R002", False), _assessment("R003__R004", True)],
    )


def _agreement_report():
    return AgreementReport(
        evaluation_component="precision",
        reviewer_a="alice",
        reviewer_b="bob",
        n_pairs=2,
        dimensions=[
            DimensionAgreement(
                field="case_difficulty",
                n_compared=2,
                n_agree=2,
                percent_agreement=1.0,
                cohens_kappa=None,
            ),
            DimensionAgreement(
                field="never_event",
                n_compared=1,
                n_agree=0,
                percent_agreement=0.0,
                cohens_kappa=None,
            ),
        ],
        overall_percent_agreement=0.75,
    )


def _consensus_result():
    report = DiscrepancyReport(
        evaluation_component="precision",
        reviewer_a="alice",
        reviewer_b="bob",
        pair_discrepancies=[
            PairDiscrepancy(pair_id="R001__R002", dimension_disagreements=[]),
            PairDiscrepancy(
                pair_id="R003__R004",
                dimension_disagreements=[
                    DimensionDisagreement(field="never_event", value_a="no", value_b="yes")
                ],
            ),
        ],
    )
    arbitration = ArbitrationSubmission(
        arbitration_id="arb-1",
        arbiter_id="carol",
        evaluation_component="precision",
        sample_checksum="0" * 64,
        decisions=[
            ArbitrationDecision(
                pair_id="R003__R004",
                consensus_dimensions={"never_event": "no"},
                free_text_materially_different=False,
                consensus_free_text="Agreed after discussion.",
                arbiter_rationale="No never-event.",
            ),
        ],
    )
    consensus = [
        ConsensusAssessment(
            pair_id="R001__R002",
            case_difficulty="non_trivial",
            reference_label_issue="no_apparent_issue",
            dimension_sources={"case_difficulty": "reviewer_agreement"},
            reviewer_free_texts={"alice": "Decisive.", "bob": "Clear duplicate."},
        ),
        ConsensusAssessment(
            pair_id="R003__R004",
            case_difficulty="non_trivial",
            reference_label_issue="no_apparent_issue",
            never_event="no",
            unexpected_method_behaviour="no",
            material_input_data_limitation="no",
            primary_error_source="relationship_assessment",
            implicated_feature_domains=["narrative"],
            dimension_sources={"never_event": "arbitration"},
            reviewer_free_texts={"alice": "Root cause X.", "bob": "Root cause Y."},
            consensus_free_text="Agreed after discussion.",
            arbiter_rationale="No never-event.",
            arbitrated=True,
        ),
    ]
    return SCLEConsensusResult(
        evaluation_component="precision",
        reviewer_submissions=[_reviewer_submission("alice"), _reviewer_submission("bob")],
        submission_checksums={"alice": "a" * 64, "bob": "b" * 64},
        discrepancy_report=report,
        arbitration=arbitration,
        consensus=consensus,
        reference_label_concerns=[],
    )


def _manifest():
    return EvaluationRunManifest(
        created_at=NOW,
        run_id="abc123def456",
        stage="analysis",
        python_version="3.11.8",
        inputs=[ArtifactRef(path="inputs/reference_set.json", sha256="a" * 64, n_bytes=1024)],
        outputs=[
            ArtifactRef(path="results/precision_metrics.csv", sha256="b" * 64, n_bytes=2048)
        ],
        ci_method="wilson",
    )


MODELS = {
    "study_reference_set": (ReferenceSet, _reference_set()),
    "study_prediction_set": (PredictionSet, _prediction_set()),
    "study_sampling_record": (SamplingRecord, _sampling_record()),
    "study_scle_sample": (SCLESample, _scle_sample()),
    "study_reviewer_submission": (ReviewerSubmission, _reviewer_submission("alice")),
    "study_agreement_report": (AgreementReport, _agreement_report()),
    "study_scle_consensus_result": (SCLEConsensusResult, _consensus_result()),
    "study_evaluation_run_manifest": (EvaluationRunManifest, _manifest()),
}


def main():
    SCHEMAS.mkdir(exist_ok=True)
    EXAMPLES.mkdir(exist_ok=True)
    for name, (model, instance) in MODELS.items():
        (SCHEMAS / f"{name}.schema.json").write_text(
            json.dumps(model.model_json_schema(), indent=2) + "\n"
        )
        (EXAMPLES / f"{name}_example.json").write_text(
            instance.model_dump_json(indent=2) + "\n"
        )
        reloaded = model.model_validate_json((EXAMPLES / f"{name}_example.json").read_text())
        assert json.loads(reloaded.model_dump_json()) == json.loads(
            instance.model_dump_json()
        ), name
        print("wrote", name)


if __name__ == "__main__":
    main()
