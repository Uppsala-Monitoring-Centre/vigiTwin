"""Regenerate the publication-mode schemas and their examples.

Run from the repository root::

    .venv/bin/python scripts/generate_publication_schemas.py

Idempotent: it rewrites both files from the models, so the drift test in
``tests/test_schemas_examples.py`` is the only thing that has to notice when a
model changes.

Both examples are *computed* — built by running the real transform over a
synthetic pair — rather than hand-written, so they cannot describe a shape the
code does not produce. Everything machine-local is stamped out afterwards, as
``scripts/generate_characterization_schema.py`` does for the same reason: a
committed example carrying this machine's git commit and installed version
fails its round-trip on every other checkout.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import SecretStr

from llm_dup_dev.models.enums import DrugRole, ReportingContextLevel
from llm_dup_dev.models.pair_features import (
    AnchorPopulation,
    PairFeatures,
    ProbabilityEstimate,
    ReportingContext,
)
from llm_dup_dev.models.enums import AnchorType
from llm_dup_dev.models.report import (
    AdverseEvent,
    AgeValue,
    DateValue,
    Drug,
    PartialDate,
    Report,
    SourcedValue,
)
from llm_dup_dev.review_html.evidence.build import build_evidence_case
from llm_dup_dev.review_html.evidence.models import EvidencePageVM, number_cases
from llm_dup_dev.review_html.evidence.publication import (
    PublicationSecrets,
    PublicationTransformManifest,
    ScramblingKeyRecord,
    build_key_record,
    build_publication_manifest,
    scramble_page,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"
EXAMPLES_DIR = REPO_ROOT / "examples"

#: Fixed, so the committed examples are reproducible. It is a synthetic salt
#: over synthetic reports; the real one never leaves the secure key store.
_EXAMPLE_SALT = "example-publication-salt"


def _report(report_id: str, *, onset: str) -> Report:
    return Report(
        report_id=report_id,
        patient_sex_values=[SourcedValue(value="female", sources=["structured"])],
        patient_age_values=[
            AgeValue(value=81, unit="years", sources=["structured"])
        ],
        country_of_origin_values=[
            SourcedValue(value="SE", sources=["structured"])
        ],
        report_date_values=[
            DateValue(
                value="2024-02-20", resolution="day", sources=["structured"]
            )
        ],
        drugs=[
            Drug(
                substance="levonorgestrel",
                sources=["structured"],
                role=DrugRole.SUSPECTED,
                start_date=PartialDate(value="2023", resolution="year"),
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


def _result():
    features = PairFeatures(
        pair_id="R1__R2",
        reporting_context=ReportingContext(
            level=ReportingContextLevel.COUNTRY_YEAR, country="SE", year=2024
        ),
        matching_drugs=["levonorgestrel"],
        matching_AEs=["device expulsion"],
        anchor=AnchorPopulation(
            N_drugs=10,
            N_AEs=10,
            N_drugs_AEs=5,
            type=AnchorType.DRUGS_AES,
            N_anchor=5,
        ),
        probability=ProbabilityEstimate(p_joint=1e-7, p_match=1e-7),
        L_match=3.0,
        L_patient=1.0,
    )
    case = build_evidence_case(
        report_1=_report("R1", onset="2023-11-13"),
        report_2=_report("R2", onset="2023-11-12"),
        pair_id="R1__R2",
        features=features,
    )
    page = EvidencePageVM(
        title="Evidence review",
        generated_at="2026-01-01T00:00:00+00:00",
        cases=number_cases([case]),
    )
    return scramble_page(
        page, secrets=PublicationSecrets(salt=SecretStr(_EXAMPLE_SALT))
    )


def _stamp_out_machine_locals(payload: dict) -> dict:
    """Replace anything that would differ between two checkouts."""
    payload["created_at"] = "2026-01-01T00:00:00+00:00"
    payload["dev_package_version"] = "1.2.0"
    payload["dev_git_commit"] = "0" * 40
    return payload


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(REPO_ROOT)}")


def main() -> None:
    result = _result()
    for name, model, record in (
        (
            "evidence_publication_manifest",
            PublicationTransformManifest,
            build_publication_manifest(result),
        ),
        (
            "evidence_scrambling_key",
            ScramblingKeyRecord,
            build_key_record(result),
        ),
    ):
        _write(SCHEMAS_DIR / f"{name}.schema.json", model.model_json_schema())
        _write(
            EXAMPLES_DIR / f"{name}_example.json",
            _stamp_out_machine_locals(record.model_dump(mode="json")),
        )


if __name__ == "__main__":
    main()
