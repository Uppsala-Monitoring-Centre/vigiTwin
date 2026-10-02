"""Tests for the `python -m llm_dup_dev.study_eval` command line.

The CLI had no tests at all — 0% coverage over 78 statements — while being a
documented, user-facing entry point. Nothing exercised it, so a rename in
`workflow` or a mistyped argparse destination would have broken it silently and
stayed broken. These tests cover the parser wiring, the error paths, and one
real end-to-end `analyze` run against synthetic files on disk.
"""

from __future__ import annotations

import json

import pytest

from llm_dup_dev.study_eval.cli import build_parser, main

SUBCOMMANDS = ("analyze", "workbooks", "arbitrate", "finalize")


def test_parser_builds():
    """Catches argparse misconfiguration, which only surfaces at build time."""
    assert build_parser() is not None


@pytest.mark.parametrize("name", SUBCOMMANDS)
def test_every_documented_subcommand_is_registered(name):
    """The module docstring advertises these four; they must all exist."""
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args([name, "--help"])
    assert exc.value.code == 0


def test_help_exits_zero():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--help"])
    assert exc.value.code == 0


def test_a_subcommand_is_required():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args([])
    assert exc.value.code != 0


def test_analyze_binds_its_handler():
    args = build_parser().parse_args(
        [
            "analyze",
            "--reference-set", "a.json",
            "--predictions", "p.json",
            "--seed", "s",
            "--output-dir", "out",
        ]
    )
    assert args.func.__name__ == "_cmd_analyze"
    assert args.reference_set == ["a.json"]  # --reference-set is repeatable


def test_unknown_variant_is_rejected_with_a_useful_message():
    """Without --positive-label the variant must name a rule the CLI carries."""
    argv = [
        "analyze",
        "--reference-set", "a.json",
        "--predictions", "p.json",
        "--seed", "s",
        "--output-dir", "out",
        "--variant", "not_a_rule",
    ]
    args = build_parser().parse_args(argv)
    from llm_dup_dev.study_eval.cli import _resolve_positive_class

    with pytest.raises(SystemExit) as exc:
        _resolve_positive_class(args)
    assert "unknown --variant" in str(exc.value)


def test_positive_label_defines_an_ad_hoc_rule():
    args = build_parser().parse_args(
        [
            "analyze",
            "--reference-set", "a.json",
            "--predictions", "p.json",
            "--seed", "s",
            "--output-dir", "out",
            "--variant", "my_rule",
            "--positive-label", "Duplicates",
            "--positive-label", "Abstain",
        ]
    )
    from llm_dup_dev.study_eval.cli import _resolve_positive_class

    rule = _resolve_positive_class(args)
    assert rule.name == "my_rule"
    assert rule.positive_labels == frozenset({"Duplicates", "Abstain"})


def test_positive_label_rejects_a_label_outside_the_vocabulary():
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "analyze",
                "--reference-set", "a.json",
                "--predictions", "p.json",
                "--seed", "s",
                "--output-dir", "out",
                "--positive-label", "NotALabel",
            ]
        )


def test_analyze_runs_end_to_end_and_writes_output(tmp_path, capsys):
    """The real thing: files in, `main()` called as a user would, files out."""
    pairs, predictions = [], {}
    for i, (ref, adj) in enumerate(
        [
            ("duplicate", "Duplicates"),
            ("duplicate", "Unrelated"),
            ("non_duplicate", "Unrelated"),
            ("non_duplicate", "Duplicates"),
        ]
    ):
        pid = f"A{i}__B{i}"
        pairs.append(
            {
                "pair_id": pid,
                "report_id1": f"A{i}",
                "report_id2": f"B{i}",
                "cluster_id": f"c{i}",
                "reference_label": ref,
            }
        )
        predictions[pid] = {
            "pair_id": pid,
            "adjudicator_label": adj,
            "screening_retrieved": True,
        }

    ref_path = tmp_path / "reference_set.json"
    ref_path.write_text(
        json.dumps({"set_id": "cli_set", "role": "precision", "pairs": pairs})
    )
    pred_path = tmp_path / "predictions.json"
    pred_path.write_text(json.dumps({"predictions": predictions}))
    out_dir = tmp_path / "out"

    code = main(
        [
            "analyze",
            "--reference-set", str(ref_path),
            "--predictions", str(pred_path),
            "--seed", "cli-test",
            "--output-dir", str(out_dir),
            "--per-cell", "1",
        ]
    )

    assert code == 0
    assert out_dir.is_dir() and list(out_dir.iterdir()), "analyze wrote nothing"
    assert "precision set(s)" in capsys.readouterr().out


def test_analyze_honours_no_scle(tmp_path):
    """--no-scle must actually suppress sampling, not just be accepted."""
    ref_path = tmp_path / "r.json"
    ref_path.write_text(
        json.dumps(
            {
                "set_id": "s",
                "role": "precision",
                "pairs": [
                    {
                        "pair_id": "A0__B0",
                        "report_id1": "A0",
                        "report_id2": "B0",
                        "cluster_id": "c0",
                        "reference_label": "duplicate",
                    }
                ],
            }
        )
    )
    pred_path = tmp_path / "p.json"
    pred_path.write_text(
        json.dumps(
            {
                "predictions": {
                    "A0__B0": {
                        "pair_id": "A0__B0",
                        "adjudicator_label": "Duplicates",
                        "screening_retrieved": True,
                    }
                }
            }
        )
    )
    out_dir = tmp_path / "out"
    assert (
        main(
            [
                "analyze",
                "--reference-set", str(ref_path),
                "--predictions", str(pred_path),
                "--seed", "s",
                "--output-dir", str(out_dir),
                "--no-scle",
            ]
        )
        == 0
    )
    assert not list(out_dir.glob("**/scle_sample*.json"))
