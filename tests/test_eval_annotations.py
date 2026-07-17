"""Tests for llm_dup_dev.eval.annotations."""

import pytest

from llm_dup_dev.eval.annotations import derive_pair_id, load_annotations


def _write(tmp_path, text, name="annotations.csv"):
    path = tmp_path / name
    path.write_text(text)
    return path


# ===================================================================
# pair_id derivation
# ===================================================================

class TestDerivePairId:
    def test_matches_the_pipeline_construction(self):
        """Must equal feature_generation.pipeline's f"{r1}__{r2}"."""
        assert derive_pair_id("R001", "R002") == "R001__R002"

    def test_is_orientation_sensitive(self):
        """No canonical ordering exists, so the two orientations differ.

        This is why check_coverage has to report reversed matches: an
        annotation file that lists a pair the other way round produces an id
        that matches nothing.
        """
        assert derive_pair_id("R001", "R002") != derive_pair_id("R002", "R001")


# ===================================================================
# load_annotations
# ===================================================================

class TestLoadAnnotations:
    def test_tab_separated_with_report_ids(self, tmp_path):
        """The real format: tab-separated, identified by two report ids."""
        path = _write(
            tmp_path,
            "report_id1\treport_id2\tduplicates_assessment\n"
            "R001\tR002\tduplicates\n"
            "R003\tR004\tunrelated\n",
        )
        anns = load_annotations(path)
        assert [a["pair_id"] for a in anns] == ["R001__R002", "R003__R004"]
        assert [a["duplicates_assessment"] for a in anns] == [
            "duplicates", "unrelated",
        ]

    def test_comma_separated_also_works(self, tmp_path):
        """The delimiter is sniffed; the extension says csv either way."""
        path = _write(
            tmp_path,
            "report_id1,report_id2,duplicates_assessment\n"
            "R001,R002,duplicates\n",
        )
        assert load_annotations(path)[0]["pair_id"] == "R001__R002"

    def test_extra_columns_are_preserved(self, tmp_path):
        """Unknown columns survive for later stratification.

        evaluate() reads only pair_id and duplicates_assessment, so carrying
        the rest costs nothing and avoids having to know them in advance.
        """
        path = _write(
            tmp_path,
            "report_id1\treport_id2\tduplicates_assessment\tannotator\tnotes\n"
            "R001\tR002\tduplicates\tnn\tclear cut\n",
        )
        ann = load_annotations(path)[0]
        assert ann["annotator"] == "nn"
        assert ann["notes"] == "clear cut"

    def test_all_four_label_values_accepted(self, tmp_path):
        """Including abstain, which is outside FINAL_LABELS."""
        path = _write(
            tmp_path,
            "report_id1\treport_id2\tduplicates_assessment\n"
            "R1\tR2\tduplicates\n"
            "R3\tR4\totherwise_related\n"
            "R5\tR6\tunrelated\n"
            "R7\tR8\tabstain\n",
        )
        assert len(load_annotations(path)) == 4

    def test_labels_are_case_insensitive(self, tmp_path):
        """normalise_label already tolerates case; the loader must not be stricter."""
        path = _write(
            tmp_path,
            "report_id1\treport_id2\tduplicates_assessment\n"
            "R001\tR002\tDuplicates\n",
        )
        assert load_annotations(path)[0]["duplicates_assessment"] == "Duplicates"

    def test_whitespace_is_stripped(self, tmp_path):
        path = _write(
            tmp_path,
            "report_id1,report_id2,duplicates_assessment\n"
            " R001 , R002 , duplicates \n",
        )
        assert load_annotations(path)[0]["pair_id"] == "R001__R002"

    # -- failures, all at load time --------------------------------

    def test_duplicate_pair_raises(self, tmp_path):
        """Two rows for one pair must not be silently de-duplicated.

        evaluate() indexes by pair_id and would keep whichever row came last,
        scoring against ground truth the caller never chose.
        """
        path = _write(
            tmp_path,
            "report_id1\treport_id2\tduplicates_assessment\n"
            "R001\tR002\tduplicates\n"
            "R001\tR002\tunrelated\n",
        )
        with pytest.raises(ValueError, match="duplicate pair_id"):
            load_annotations(path)

    def test_bad_label_raises_at_load_not_mid_run(self, tmp_path):
        """A typo must fail before a sweep spends anything."""
        path = _write(
            tmp_path,
            "report_id1\treport_id2\tduplicates_assessment\n"
            "R001\tR002\tduplicate\n",  # missing the 's'
        )
        with pytest.raises(ValueError, match="duplicate"):
            load_annotations(path)

    def test_missing_column_raises_with_what_was_found(self, tmp_path):
        path = _write(
            tmp_path,
            "pair_id\tduplicates_assessment\nR001__R002\tduplicates\n",
        )
        with pytest.raises(ValueError, match="missing required column"):
            load_annotations(path)

    def test_blank_report_id_raises(self, tmp_path):
        """A blank id would silently derive the pair_id 'R001__'."""
        path = _write(
            tmp_path,
            "report_id1\treport_id2\tduplicates_assessment\n"
            "R001\t\tduplicates\n",
        )
        with pytest.raises(ValueError, match="blank report id"):
            load_annotations(path)

    def test_empty_file_raises(self, tmp_path):
        with pytest.raises(ValueError, match="empty"):
            load_annotations(_write(tmp_path, ""))

    def test_error_names_the_line(self, tmp_path):
        """Errors must point at the row, counting the header as line 1."""
        path = _write(
            tmp_path,
            "report_id1\treport_id2\tduplicates_assessment\n"
            "R001\tR002\tduplicates\n"
            "R003\tR004\tnonsense\n",
        )
        with pytest.raises(ValueError, match=":3:"):
            load_annotations(path)


class TestLoadedAnnotationsFeedEvaluate:
    """The loader's output must be what evaluate() already consumes."""

    def test_round_trips_through_evaluate(self, tmp_path):
        from llm_dup_dev.eval import evaluate

        path = _write(
            tmp_path,
            "report_id1\treport_id2\tduplicates_assessment\n"
            "R001\tR002\tduplicates\n",
        )
        anns = load_annotations(path)
        summary = evaluate(
            pipeline_results=[{"pair_id": "R001__R002", "final_label": "Duplicates"}],
            eval_annotations=anns,
            experiment_id="t",
        )
        assert summary.n_pairs == 1
        assert summary.duplicates_vs_nonduplicates.true_positives == 1
