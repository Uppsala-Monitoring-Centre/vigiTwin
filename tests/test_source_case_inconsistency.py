"""Tests for the verdict → Inconsistency mapping helper."""

import pytest

from llm_dup_dev.feature_generation.source_case_inconsistency import (
    source_case_findings,
)
from llm_dup_dev.models.enums import InconsistencySeverity
from llm_dup_dev.models.narrative_source_case import (
    SourceCaseAssessment,
    SourceCaseVerdict,
    SourceIdentifierType,
)


class TestNoFindings:

    def test_none_assessment_yields_empty(self):
        assert source_case_findings(None) == []

    @pytest.mark.parametrize("verdict", [
        SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT,
        SourceCaseVerdict.SAME_SOURCE_SAME_CASE,
        SourceCaseVerdict.MULTI_PATIENT_CONTEXT_NO_LINK,
        SourceCaseVerdict.NO_DECISIVE_SIGNAL,
        SourceCaseVerdict.NOT_ASSESSED,
    ])
    def test_non_inconsistency_verdicts_yield_empty(self, verdict):
        assert source_case_findings(SourceCaseAssessment(verdict=verdict)) == []


class TestSameSourceDifferentCase:

    def test_emits_single_decisive_finding(self):
        assessment = SourceCaseAssessment(
            verdict=SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE,
            shared_source_identifier="10.1234/abc",
            shared_source_identifier_type=SourceIdentifierType.DOI,
        )
        findings = source_case_findings(assessment)
        assert len(findings) == 1
        f = findings[0]
        assert f.severity == InconsistencySeverity.DECISIVE
        assert f.source == "narrative_source_case"
        assert f.kind == SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE.value
        assert f.feature == "source_case_same_source_different_case"

    def test_rationale_includes_shared_identifier(self):
        assessment = SourceCaseAssessment(
            verdict=SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE,
            shared_source_identifier="10.1234/abc",
            shared_source_identifier_type=SourceIdentifierType.DOI,
        )
        f = source_case_findings(assessment)[0]
        assert "doi=10.1234/abc" in f.rationale
        assert "same_source_different_case" in f.rationale


class TestSameSourceCaseUncomparable:

    def test_emits_single_moderate_finding(self):
        assessment = SourceCaseAssessment(
            verdict=SourceCaseVerdict.SAME_SOURCE_CASE_UNCOMPARABLE,
            shared_source_identifier="10.xyz/abc",
            shared_source_identifier_type=SourceIdentifierType.DOI,
        )
        findings = source_case_findings(assessment)
        assert len(findings) == 1
        f = findings[0]
        assert f.severity == InconsistencySeverity.MODERATE
        assert f.source == "narrative_source_case"
        assert f.kind == SourceCaseVerdict.SAME_SOURCE_CASE_UNCOMPARABLE.value
        assert f.feature == "source_case_same_source_case_uncomparable"

    def test_rationale_without_shared_identifier(self):
        assessment = SourceCaseAssessment(
            verdict=SourceCaseVerdict.SAME_SOURCE_CASE_UNCOMPARABLE,
        )
        f = source_case_findings(assessment)[0]
        # No identifier present — rationale still carries the verdict.
        assert "same_source_case_uncomparable" in f.rationale
