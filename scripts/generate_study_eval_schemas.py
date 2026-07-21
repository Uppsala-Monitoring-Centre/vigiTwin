"""Generate committed schema + example JSON for the study_eval models.

Writes schemas/study_*.schema.json and examples/study_*_example.json. Run from
the repo root. Idempotent.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from llm_dup_dev.study_eval.schemas import (
    AdjudicatorOutput,
    MethodOutputs,
    PrecisionEvaluationSet,
    PrecisionPairRecord,
    RecallEvaluationSet,
    RecallPairRecord,
)
from llm_dup_dev.study_eval.records import SamplingRecord
from llm_dup_dev.study_eval.sampling import SCLECase, SCLESample
from llm_dup_dev.study_eval.scle.form import (
    CaseDifficulty,
    ReferenceLabelIssue,
    ReviewerSubmission,
    SCLEAutoContext,
    SCLECaseAssessment,
    YesNoUnclear,
    PrimaryErrorSource,
)
from llm_dup_dev.study_eval.scle.discrepancy import (
    DimensionDisagreement,
    DiscrepancyReport,
    PairDiscrepancy,
)
from llm_dup_dev.study_eval.scle.arbitration import (
    ArbitrationDecision,
    ArbitrationSubmission,
)
from llm_dup_dev.study_eval.scle.consensus import (
    ConsensusAssessment,
    SCLEConsensusResult,
)
from llm_dup_dev.study_eval.manifest import (
    ArtifactRef,
    EvaluationRunManifest,
)

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "schemas"
EXAMPLES = ROOT / "examples"

NOW = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)


def _precision_set():
    return PrecisionEvaluationSet(
        set_id="vigimatch2025_precision_50",
        screening_method="vigiMatch2025",
        snapshot_description="VigiBase up to 2026-01-28",
        pairs=[
            PrecisionPairRecord(
                pair_id="R001__R002",
                report_id1="R001",
                report_id2="R002",
                cluster_id="C1",
                reference_label="duplicate",
                annotator_id="ref-annotator",
                annotated_at="2026-03-15",
            ),
            PrecisionPairRecord(
                pair_id="R003__R004",
                report_id1="R003",
                report_id2="R004",
                cluster_id="C2",
                reference_label="non_duplicate",
            ),
        ],
    )


def _recall_set():
    return RecallEvaluationSet(
        reference_set_id="fda_gold",
        reference_set_label="FDA Gold",
        pairs=[
            RecallPairRecord(
                pair_id="R101__R102",
                report_id1="R101",
                report_id2="R102",
                cluster_id="G1",
                provenance="manual",
            ),
            RecallPairRecord(
                pair_id="R103__R104",
                report_id1="R103",
                report_id2="R104",
                cluster_id="G2",
                provenance="e2b_derived",
            ),
        ],
    )


def _method_outputs():
    return MethodOutputs(
        variant_id="primary",
        adjudicator={
            "R001__R002": AdjudicatorOutput(
                pair_id="R001__R002", final_label="Duplicates"
            ),
            "R003__R004": AdjudicatorOutput(
                pair_id="R003__R004", final_label="Unrelated"
            ),
        },
        vigimatch_retrieved={"R001__R002": True, "R003__R004": True},
    )


def _sampling_record():
    return SamplingRecord(
        procedure_id="one_per_cluster_v1",
        seed="study-2026:fda_gold",
        created_at=NOW,
        package_version="0.1.0",
        python_version="3.11.8",
        target=100,
        n_eligible_clusters=87,
        tier_counts={"manual": 60, "e2b_derived": 40},
        selected_pair_ids=["R101__R102", "R103__R104"],
    )


def _auto_context(pair_id, cell, mis, disagree):
    return SCLEAutoContext(
        pair_id=pair_id,
        evaluation_component="precision",
        reference_label="duplicate",
        adjudicator_final_label="Duplicates" if not mis else "Otherwise_related",
        vigimatch_retrieved=True,
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
        records=[
            SamplingRecord(
                procedure_id="scle_precision_v1:TP",
                seed="study-2026",
                created_at=NOW,
                target=10,
                stratum_sizes={"TP": 22},
                selected_pair_ids=["R001__R002"],
            )
        ],
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
        assessments=[
            _assessment("R001__R002", False),
            _assessment("R003__R004", True),
        ],
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
                    DimensionDisagreement(
                        field="never_event", value_a="no", value_b="yes"
                    )
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
                pair_id="R001__R002",
                consensus_dimensions={},
                free_text_materially_different=False,
                consensus_free_text="Agreed.",
                arbiter_rationale="",
            ),
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
            free_text_assessment="Agreed.",
            case_difficulty="non_trivial",
            reference_label_issue="no_apparent_issue",
            dimension_sources={"case_difficulty": "reviewer_agreement"},
        ),
        ConsensusAssessment(
            pair_id="R003__R004",
            free_text_assessment="Agreed after discussion.",
            case_difficulty="non_trivial",
            reference_label_issue="no_apparent_issue",
            never_event="no",
            unexpected_method_behaviour="no",
            material_input_data_limitation="no",
            primary_error_source="relationship_assessment",
            implicated_feature_domains=["narrative"],
            dimension_sources={"never_event": "arbitration"},
            arbiter_rationale="No never-event.",
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
        stage="aggregate",
        python_version="3.11.8",
        inputs=[
            ArtifactRef(path="inputs/pset.json", sha256="a" * 64, n_bytes=1024)
        ],
        outputs=[
            ArtifactRef(
                path="results/precision_metrics.csv", sha256="b" * 64, n_bytes=2048
            )
        ],
        ci_method="wilson",
    )


MODELS = {
    "study_precision_evaluation_set": (PrecisionEvaluationSet, _precision_set()),
    "study_recall_evaluation_set": (RecallEvaluationSet, _recall_set()),
    "study_method_outputs": (MethodOutputs, _method_outputs()),
    "study_sampling_record": (SamplingRecord, _sampling_record()),
    "study_scle_sample": (SCLESample, _scle_sample()),
    "study_reviewer_submission": (ReviewerSubmission, _reviewer_submission("alice")),
    "study_scle_consensus_result": (SCLEConsensusResult, _consensus_result()),
    "study_evaluation_run_manifest": (EvaluationRunManifest, _manifest()),
}


def main():
    SCHEMAS.mkdir(exist_ok=True)
    EXAMPLES.mkdir(exist_ok=True)
    for name, (model, instance) in MODELS.items():
        schema = model.model_json_schema()
        (SCHEMAS / f"{name}.schema.json").write_text(
            json.dumps(schema, indent=2) + "\n"
        )
        (EXAMPLES / f"{name}_example.json").write_text(
            instance.model_dump_json(indent=2) + "\n"
        )
        # verify round-trip
        reloaded = model.model_validate_json((EXAMPLES / f"{name}_example.json").read_text())
        assert json.loads(reloaded.model_dump_json()) == json.loads(
            instance.model_dump_json()
        ), name
        print("wrote", name)


if __name__ == "__main__":
    main()
