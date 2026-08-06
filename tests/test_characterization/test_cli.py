"""``python -m llm_dup_dev.characterization`` end to end over temp files."""

from __future__ import annotations

import json

import pytest

from llm_dup_dev.characterization import (
    JSON_FILENAME,
    MARKDOWN_FILENAME,
    CharacterizationReport,
)
from llm_dup_dev.characterization.cli import main

from .conftest import make_reference_set, sourced


@pytest.fixture
def workspace(tmp_path, report_a, report_b, report_c):
    reports_path = tmp_path / "reports.json"
    reports_path.write_text(
        json.dumps([r.model_dump(mode="json") for r in (report_a, report_b, report_c)])
    )

    set_path = tmp_path / "precision.json"
    reference_set = make_reference_set(
        [("R001", "R002")], set_id="precision_primary", provenance="manual"
    )
    set_path.write_text(reference_set.model_dump_json(indent=2))

    return tmp_path, set_path, reports_path


def test_writes_json_and_markdown(workspace):
    tmp_path, set_path, reports_path = workspace
    out = tmp_path / "out"

    assert main(
        [
            "--reference-set", str(set_path),
            "--reports", str(reports_path),
            "--output-dir", str(out),
        ]
    ) == 0

    payload = CharacterizationReport.model_validate_json(
        (out / JSON_FILENAME).read_text()
    )
    assert [block.set_id for block in payload.sets] == ["precision_primary"]
    assert (out / MARKDOWN_FILENAME).exists()


def test_reads_the_same_reference_set_files_study_eval_reads(workspace):
    """No separate export: the CLI consumes study_reference_set.schema.json."""
    tmp_path, set_path, reports_path = workspace
    from llm_dup_dev.study_eval.loading import load_reference_set

    assert load_reference_set(set_path).set_id == "precision_primary"


def test_top_n_and_dataset_id_reach_the_artifact(workspace):
    tmp_path, set_path, reports_path = workspace
    out = tmp_path / "out"

    main(
        [
            "--reference-set", str(set_path),
            "--reports", str(reports_path),
            "--output-dir", str(out),
            "--top-n", "3",
            "--dataset-id", "ds-42",
            "--notes", "pre-merge reports",
        ]
    )
    payload = CharacterizationReport.model_validate_json(
        (out / JSON_FILENAME).read_text()
    )

    assert payload.policy.top_n == 3
    assert payload.dataset_id == "ds-42"
    assert payload.notes == "pre-merge reports"


def test_csv_requires_asking_for_it(workspace):
    tmp_path, set_path, reports_path = workspace
    out = tmp_path / "out"

    main(
        [
            "--reference-set", str(set_path),
            "--reports", str(reports_path),
            "--output-dir", str(out),
            "--format", "csv",
        ]
    )

    assert (out / "reference_set_characteristics.csv").exists()
    assert not (out / JSON_FILENAME).exists()


def test_missing_report_fails_unless_allowed(tmp_path, report_a):
    reports_path = tmp_path / "reports.json"
    reports_path.write_text(json.dumps([report_a.model_dump(mode="json")]))
    set_path = tmp_path / "set.json"
    set_path.write_text(make_reference_set([("R001", "R002")]).model_dump_json())

    argv = [
        "--reference-set", str(set_path),
        "--reports", str(reports_path),
        "--output-dir", str(tmp_path / "out"),
    ]
    with pytest.raises(KeyError):
        main(argv)

    assert main([*argv, "--allow-missing-reports"]) == 0


def test_duplicate_report_id_is_rejected_rather_than_overwritten(tmp_path, report_a):
    """Two entries under one id may differ; picking one silently is not safe."""
    reports_path = tmp_path / "reports.json"
    duplicate = report_a.model_copy(
        update={"country_of_origin_values": [sourced("DE")]}
    )
    reports_path.write_text(
        json.dumps([r.model_dump(mode="json") for r in (report_a, duplicate)])
    )
    set_path = tmp_path / "set.json"
    set_path.write_text(make_reference_set([("R001", "R002")]).model_dump_json())

    with pytest.raises(ValueError, match="duplicate report_id"):
        main(
            [
                "--reference-set", str(set_path),
                "--reports", str(reports_path),
                "--output-dir", str(tmp_path / "out"),
            ]
        )


def test_reports_may_be_a_mapping_keyed_by_report_id(tmp_path, report_a, report_b):
    """The review_html shape works too — the two frozen conventions coexist."""
    reports_path = tmp_path / "reports.json"
    reports_path.write_text(
        json.dumps({r.report_id: r.model_dump(mode="json") for r in (report_a, report_b)})
    )
    set_path = tmp_path / "set.json"
    set_path.write_text(make_reference_set([("R001", "R002")]).model_dump_json())
    out = tmp_path / "out"

    assert main(
        [
            "--reference-set", str(set_path),
            "--reports", str(reports_path),
            "--output-dir", str(out),
        ]
    ) == 0
    payload = CharacterizationReport.model_validate_json((out / JSON_FILENAME).read_text())
    assert payload.sets[0].reports.n_unique_reports == 2


def test_reports_file_must_be_a_list_or_a_mapping(tmp_path):
    reports_path = tmp_path / "reports.json"
    reports_path.write_text(json.dumps("not an artifact"))
    set_path = tmp_path / "set.json"
    set_path.write_text(make_reference_set([("R001", "R002")]).model_dump_json())

    with pytest.raises(ValueError, match="must be a list or an object"):
        main(
            [
                "--reference-set", str(set_path),
                "--reports", str(reports_path),
                "--output-dir", str(tmp_path / "out"),
            ]
        )


def test_missing_required_arguments_exit_non_zero(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        main(["--output-dir", str(tmp_path)])

    assert excinfo.value.code != 0
