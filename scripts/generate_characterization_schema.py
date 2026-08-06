"""Generate the committed schema + example for the characterization artifact.

Writes ``schemas/characterization_report.schema.json`` and
``examples/characterization_report_example.json``. Run from the repo root.
Idempotent; drift-checked by ``tests/test_schemas_examples.py``.

The example is *computed* — the per-set blocks come from the real builder over
synthetic reports — so it stays honest as the statistics evolve, and so the
committed file documents the interesting cases rather than a happy path. The
synthetic reports below between them exhibit a multi-valued age, a missing
country, an adverse event with a MedDRA HLT and one without, a drug with no
recorded role, and a drug shared across a pair.

The outer ``CharacterizationReport`` is assembled **by hand** rather than via
``build_characterization_report``, which would stamp in this machine's git
commit and installed package version and make the committed example fail its
byte-identical round-trip on any other checkout. Same trick
``generate_study_eval_schemas.py`` uses for ``EvaluationRunManifest``.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from llm_dup_dev.characterization.builder import (
    build_reference_set_characteristics,
    resolution_policy_summary,
)
from llm_dup_dev.characterization.models import CharacterizationReport
from llm_dup_dev.models.enums import DrugRole
from llm_dup_dev.models.report import (
    AdverseEvent,
    AgeValue,
    DateValue,
    Drug,
    Report,
    SourcedValue,
)
from llm_dup_dev.study_eval.schemas import ReferencePair, ReferenceSet

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "schemas"
EXAMPLES = ROOT / "examples"

NAME = "characterization_report"
NOW = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
TOP_N = 10


def _reports() -> dict[str, Report]:
    return {
        "R001": Report(
            report_id="R001",
            patient_sex_values=[SourcedValue(value="female", sources=["structured"])],
            patient_age_values=[AgeValue(value=54, unit="years", sources=["structured"])],
            country_of_origin_values=[SourcedValue(value="US", sources=["structured"])],
            report_date_values=[
                DateValue(value="2020-06-12", resolution="day", sources=["structured"])
            ],
            drugs=[
                Drug(
                    substance="pregabalin",
                    sources=["structured"],
                    role=DrugRole.SUSPECTED,
                ),
                # No role recorded — excluded from the top-drugs table, and
                # visible as such in the drug-roles distribution.
                Drug(substance="omeprazole", sources=["structured"]),
            ],
            adverse_events=[
                AdverseEvent(
                    pt_name="somnolence",
                    sources=["structured"],
                    primary_hlt_name="disturbances in consciousness nec",
                ),
                # No MedDRA hierarchy — counted at PT level only.
                AdverseEvent(pt_name="dizziness", sources=["structured"]),
            ],
            narrative="Patient experienced somnolence after starting pregabalin.",
        ),
        "R002": Report(
            report_id="R002",
            patient_sex_values=[SourcedValue(value="female", sources=["structured"])],
            # Two candidates from different sources: structured wins, so this
            # report tabulates as 54 and does not conflict with R001.
            patient_age_values=[
                AgeValue(value=54, unit="years", sources=["structured"]),
                AgeValue(value=55, unit="years", sources=["narrative_llm"]),
            ],
            country_of_origin_values=[SourcedValue(value="US", sources=["structured"])],
            report_date_values=[
                DateValue(value="2020", resolution="year", sources=["structured"])
            ],
            drugs=[
                Drug(
                    substance="pregabalin",
                    sources=["structured"],
                    role=DrugRole.SUSPECTED,
                ),
            ],
            adverse_events=[
                AdverseEvent(
                    pt_name="somnolence",
                    sources=["structured"],
                    primary_hlt_name="disturbances in consciousness nec",
                ),
            ],
            narrative="Female patient reports drowsiness following pregabalin.",
        ),
        "R003": Report(
            report_id="R003",
            patient_sex_values=[SourcedValue(value="male", sources=["structured"])],
            patient_age_values=[AgeValue(value=28, unit="years", sources=["structured"])],
            # No country recorded — a missing value, not a category.
            report_date_values=[
                DateValue(value="2022-01-10", resolution="day", sources=["structured"])
            ],
            drugs=[
                Drug(
                    substance="metformin",
                    sources=["structured"],
                    role=DrugRole.SUSPECTED,
                ),
            ],
            adverse_events=[
                AdverseEvent(
                    pt_name="headache",
                    sources=["structured"],
                    primary_hlt_name="headaches nec",
                ),
            ],
        ),
        "R004": Report(report_id="R004"),
    }


def _precision_set() -> ReferenceSet:
    return ReferenceSet(
        set_id="precision_primary",
        role="precision",
        label="Screening candidate set",
        provenance="manual_annotation",
        pairs=[
            ReferencePair(
                pair_id="R001__R002",
                report_id1="R001",
                report_id2="R002",
                cluster_id="C1",
                reference_label="duplicate",
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


def _recall_set() -> ReferenceSet:
    return ReferenceSet(
        set_id="recall_regulator_gold",
        role="recall",
        provenance="regulator_reference_set",
        screening_mode="not_evaluated",
        pairs=[
            ReferencePair(
                pair_id="R001__R003", report_id1="R001", report_id2="R003"
            ),
        ],
    )


def _report() -> CharacterizationReport:
    reports = _reports()
    return CharacterizationReport(
        created_at=NOW,
        characterization_id="abc123def456",
        dataset_id="dataset0001ab",
        policy=resolution_policy_summary(TOP_N),
        sets=[
            build_reference_set_characteristics(_precision_set(), reports, top_n=TOP_N),
            build_reference_set_characteristics(_recall_set(), reports, top_n=TOP_N),
        ],
        notes="Synthetic reports; illustrates the committed artifact shape.",
    )


def main() -> None:
    SCHEMAS.mkdir(exist_ok=True)
    EXAMPLES.mkdir(exist_ok=True)

    instance = _report()
    (SCHEMAS / f"{NAME}.schema.json").write_text(
        json.dumps(CharacterizationReport.model_json_schema(), indent=2) + "\n"
    )
    example_path = EXAMPLES / f"{NAME}_example.json"
    example_path.write_text(instance.model_dump_json(indent=2) + "\n")

    reloaded = CharacterizationReport.model_validate_json(example_path.read_text())
    assert json.loads(reloaded.model_dump_json()) == json.loads(
        instance.model_dump_json()
    ), NAME
    print("wrote", NAME)


if __name__ == "__main__":
    main()
