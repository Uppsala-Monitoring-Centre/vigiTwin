"""Reading a reference set's own annotation vocabulary.

Only three terms are built in — ``duplicate`` / ``non_duplicate`` /
``abstain`` — because those are the study's binary reference vocabulary and
the only thing an estimand knows. A reference set is free to annotate in its
own words, and how those words were read in the analysis is a study decision
this package cannot infer.

What it must not do is drop one silently. An unreadable label and an absent
one both showed "Not supplied", which is how a whole set's annotations can go
unread with nobody noticing.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from llm_dup_dev.review_html.evidence.build import normalise_reference_term
from llm_dup_dev.review_html.evidence.manifest import CuratedExampleManifest
from llm_dup_dev.review_html.formats import reference_from_annotation


def _dataset(**extra) -> dict:
    base = {
        "reports": "reports.json",
        "pairs": "pairs.json",
        "pair_results": "results.json",
    }
    base.update(extra)
    return base


def _manifest(**overrides) -> CuratedExampleManifest:
    base = dict(
        datasets={"d": _dataset()},
        examples=[{"dataset": "d", "pair_id": "R001__R002"}],
    )
    base.update(overrides)
    return CuratedExampleManifest(**base)


class TestTheBuiltInVocabulary:
    def test_the_three_study_terms_are_read(self):
        for raw in ("duplicate", "non_duplicate", "abstain"):
            info = reference_from_annotation({"duplicates_assessment": raw})
            assert info.reference_label == raw
            assert info.reference_note == ""

    def test_case_and_punctuation_do_not_matter(self):
        info = reference_from_annotation(
            {"duplicates_assessment": " Non Duplicate "}
        )
        assert info.reference_label == "non_duplicate"

    def test_no_annotation_is_not_an_unreadable_one(self):
        """Nothing to read is not the same as something unread."""
        assert reference_from_annotation(None).reference_label is None
        assert reference_from_annotation(None).reference_note == ""
        empty = reference_from_annotation({"duplicates_assessment": "  "})
        assert empty.reference_label is None
        assert empty.reference_note == ""


class TestAnUnknownTermIsReported:
    def test_it_is_not_guessed_at(self):
        info = reference_from_annotation(
            {"duplicates_assessment": "Otherwise related"}
        )
        assert info.reference_label is None

    def test_it_leaves_a_note(self):
        info = reference_from_annotation(
            {"duplicates_assessment": "Otherwise related"}
        )
        assert "'Otherwise related'" in info.reference_note
        assert "reference_label_map" in info.reference_note

    def test_the_note_becomes_a_gap_on_the_case(self):
        from tests.test_review_html.test_evidence_render import _case

        case = _case(
            reference=reference_from_annotation(
                {"duplicates_assessment": "Otherwise related"}
            )
        )
        codes = [g.code.value for g in case.gaps]
        assert "reference_label_unreadable" in codes

    def test_a_pipeline_label_is_still_not_a_reference_verdict(self):
        """``Duplicates`` is a prediction; reading it as one would conflate
        the two vocabularies the whole model keeps apart."""
        info = reference_from_annotation(
            {"duplicates_assessment": "Duplicates"}
        )
        assert info.reference_label is None
        assert info.reference_note


class TestASuppliedMapping:
    def test_it_reads_the_sets_own_term(self):
        info = reference_from_annotation(
            {"duplicates_assessment": "Otherwise related"},
            label_map={"otherwise_related": "non_duplicate"},
        )
        assert info.reference_label == "non_duplicate"
        assert info.reference_note == ""

    def test_a_term_outside_the_mapping_is_still_reported(self):
        info = reference_from_annotation(
            {"duplicates_assessment": "Probably related"},
            label_map={"otherwise_related": "non_duplicate"},
        )
        assert info.reference_label is None
        assert info.reference_note

    def test_it_cannot_redefine_a_built_in_term(self):
        """A collection that could make ``duplicate`` mean its opposite is a
        reference set nobody can trust."""
        info = reference_from_annotation(
            {"duplicates_assessment": "duplicate"},
            label_map={"duplicate": "non_duplicate"},
        )
        assert info.reference_label == "duplicate"


class TestTheNormalisation:
    @pytest.mark.parametrize(
        "written",
        ["Otherwise related", "otherwise_related", "Otherwise-Related",
         "  OTHERWISE   RELATED  "],
    )
    def test_one_term_written_several_ways(self, written):
        assert normalise_reference_term(written) == "otherwise_related"

    def test_a_term_with_no_letters_folds_to_nothing(self):
        assert normalise_reference_term("  _- ") == ""


class TestTheManifestValidatesTheMapping:
    def test_a_valid_mapping_loads(self):
        manifest = _manifest(
            reference_label_map={"Otherwise related": "non_duplicate"}
        )
        assert manifest.reference_label_map_for("d") == {
            "otherwise_related": "non_duplicate"
        }

    def test_a_target_outside_the_vocabulary_is_rejected(self):
        """"Non-duplicates" is the study's class name, not a label value."""
        with pytest.raises(ValidationError, match="reference vocabulary"):
            _manifest(
                reference_label_map={"Otherwise related": "Non-duplicates"}
            )

    def test_redefining_a_built_in_term_is_rejected(self):
        with pytest.raises(ValidationError, match="may not redefine"):
            _manifest(reference_label_map={"Duplicate": "non_duplicate"})

    def test_two_keys_folding_to_one_term_are_rejected(self):
        """Which one applied would otherwise depend on dict order."""
        with pytest.raises(ValidationError, match="the same term"):
            _manifest(
                reference_label_map={
                    "Otherwise related": "non_duplicate",
                    "otherwise_related": "abstain",
                }
            )

    def test_an_empty_key_is_rejected(self):
        with pytest.raises(ValidationError, match="empty key"):
            _manifest(reference_label_map={"  ": "non_duplicate"})

    def test_no_mapping_is_the_default(self):
        assert _manifest().reference_label_map == {}
        assert _manifest().reference_label_map_for("d") == {}

    def test_a_dataset_mapping_is_validated_the_same_way(self):
        with pytest.raises(ValidationError, match="reference vocabulary"):
            _manifest(
                datasets={
                    "d": _dataset(
                        reference_label_map={"Otherwise related": "Non-duplicates"}
                    )
                }
            )

    def test_a_dataset_may_not_redefine_a_built_in_term_either(self):
        with pytest.raises(ValidationError, match="may not redefine"):
            _manifest(
                datasets={
                    "d": _dataset(
                        reference_label_map={"Duplicate": "non_duplicate"}
                    )
                }
            )


class TestWhichVocabularyADatasetReadsWith:
    """A reference label is read out of *this* dataset's annotations file.

    A collection spanning two reference sets may be spanning two
    vocabularies, which is why the mapping sits on the dataset — the same
    reason ``screening_verdict_key`` does. The collection-level field is the
    default for datasets that declare none, so a single-set collection still
    says it once.
    """

    def _two_sets(self, **collection):
        return _manifest(
            datasets={
                "precision": _dataset(
                    reference_label_map={"Otherwise related": "non_duplicate"}
                ),
                "recall": _dataset(),
            },
            examples=[
                {"dataset": "precision", "pair_id": "R001__R002"},
                {"dataset": "recall", "pair_id": "R003__R004"},
            ],
            **collection,
        )

    def test_a_dataset_uses_its_own(self):
        manifest = self._two_sets()
        assert manifest.reference_label_map_for("precision") == {
            "otherwise_related": "non_duplicate"
        }

    def test_a_dataset_without_one_falls_back_to_the_collection(self):
        manifest = self._two_sets(
            reference_label_map={"Possibly duplicate": "abstain"}
        )
        assert manifest.reference_label_map_for("recall") == {
            "possibly_duplicate": "abstain"
        }

    def test_a_dataset_mapping_replaces_rather_than_merges(self):
        """A set that declares a vocabulary gets exactly the one it declared.

        Merging would let it inherit a term its own annotators meant
        differently — and a wrongly mapped label is shown as a human verdict,
        where an unmapped one is reported as a gap.
        """
        manifest = self._two_sets(
            reference_label_map={"Possibly duplicate": "abstain"}
        )
        resolved = manifest.reference_label_map_for("precision")
        assert resolved == {"otherwise_related": "non_duplicate"}
        assert "possibly_duplicate" not in resolved

    def test_neither_level_set_reads_nothing_extra(self):
        assert self._two_sets().reference_label_map_for("recall") == {}
