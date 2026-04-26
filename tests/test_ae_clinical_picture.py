"""Tests for the AE clinical-picture inconsistency assessment task."""

from __future__ import annotations

import pytest

from llm_dup_dev.classifier.config import PipelineConfig
from llm_dup_dev.data.synthetic import (
    SyntheticReportLoader,
    SyntheticStatsProvider,
)
from llm_dup_dev.feature_generation.pipeline import generate_pair_features
from llm_dup_dev.feature_generation.probability import FrequencyStats
from llm_dup_dev.llm import LLMResponse
from llm_dup_dev.llm.fake import FakeLLMClient
from llm_dup_dev.llm.tasks.ae_clinical_picture import (
    SOURCE_LABEL,
    SUPPRESSED_BY_DECISIVE_GLOBAL,
    AEClinicalPictureVerdict,
    AEInconsistencyFindingLLM,
    AEInconsistencyKind,
    assess_ae_clinical_picture,
    build_ae_clinical_picture_request,
    collapse_subordinate_findings,
)
from llm_dup_dev.models.enums import (
    AEClinicalPictureStatus,
    InconsistencySeverity,
    NarrativeConsistency,
)
from llm_dup_dev.models.report import AdverseEvent, Report
from llm_dup_dev.models.report_pair import ReportPair
from llm_dup_dev.pipeline.runner import PipelineRunner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_client() -> FakeLLMClient:
    return FakeLLMClient()


def _report_with_aes(
    report_id: str,
    pt_names: list[str],
    *,
    narrative: str | None = None,
) -> Report:
    return Report(
        report_id=report_id,
        adverse_events=[
            AdverseEvent(pt_name=pt, sources=["structured"]) for pt in pt_names
        ],
        narrative=narrative,
    )


# ===========================================================================
# build_ae_clinical_picture_request
# ===========================================================================


class TestBuildRequest:
    def test_includes_all_aes_from_both_reports(self):
        r1 = _report_with_aes("R1", ["headache", "nausea", "dizziness"])
        r2 = _report_with_aes("R2", ["fatigue", "headache"])
        req = build_ae_clinical_picture_request(r1, r2)
        for term in ["headache", "nausea", "dizziness", "fatigue"]:
            assert term in req.user_prompt

    def test_does_not_truncate_long_ae_lists(self):
        long_list_1 = [f"event_{i}" for i in range(60)]
        long_list_2 = [f"other_event_{i}" for i in range(60)]
        r1 = _report_with_aes("R1", long_list_1)
        r2 = _report_with_aes("R2", long_list_2)
        req = build_ae_clinical_picture_request(r1, r2)
        for term in long_list_1 + long_list_2:
            assert term in req.user_prompt

    def test_includes_narratives_when_present(self):
        r1 = _report_with_aes("R1", ["headache"], narrative="Patient had headache.")
        r2 = _report_with_aes("R2", ["headache"], narrative="Headache reported.")
        req = build_ae_clinical_picture_request(r1, r2)
        assert "Patient had headache." in req.user_prompt
        assert "Headache reported." in req.user_prompt

    def test_missing_narrative_uses_fallback(self):
        r1 = _report_with_aes("R1", ["headache"], narrative=None)
        r2 = _report_with_aes("R2", ["headache"], narrative="")
        req = build_ae_clinical_picture_request(r1, r2)
        assert "no narrative available" in req.user_prompt

    def test_empty_ae_list_uses_fallback(self):
        r1 = _report_with_aes("R1", [], narrative="text")
        r2 = _report_with_aes("R2", ["headache"], narrative="text")
        req = build_ae_clinical_picture_request(r1, r2)
        assert "(none reported)" in req.user_prompt

    def test_response_model_is_verdict(self):
        r1 = _report_with_aes("R1", ["a"])
        r2 = _report_with_aes("R2", ["b"])
        req = build_ae_clinical_picture_request(r1, r2)
        assert req.response_model is AEClinicalPictureVerdict

    def test_default_temperature_is_zero(self):
        r1 = _report_with_aes("R1", ["a"])
        r2 = _report_with_aes("R2", ["b"])
        req = build_ae_clinical_picture_request(r1, r2)
        assert req.temperature == 0.0

    def test_custom_temperature(self):
        r1 = _report_with_aes("R1", ["a"])
        r2 = _report_with_aes("R2", ["b"])
        req = build_ae_clinical_picture_request(r1, r2, temperature=0.4)
        assert req.temperature == 0.4

    def test_system_prompt_excludes_chronology(self):
        r1 = _report_with_aes("R1", ["a"])
        r2 = _report_with_aes("R2", ["b"])
        req = build_ae_clinical_picture_request(r1, r2)
        # Explicit out-of-scope statement about dates / chronology
        sp = req.system_prompt.lower()
        assert "chronolog" in sp or "date" in sp
        assert "out of scope" in sp or "not focus" in sp.replace("do not focus", "not focus")

    def test_system_prompt_mentions_pharmacovigilance(self):
        r1 = _report_with_aes("R1", ["a"])
        r2 = _report_with_aes("R2", ["b"])
        req = build_ae_clinical_picture_request(r1, r2)
        assert "pharmacovigilance" in req.system_prompt.lower()

    def test_system_prompt_lists_severity_levels(self):
        r1 = _report_with_aes("R1", ["a"])
        r2 = _report_with_aes("R2", ["b"])
        req = build_ae_clinical_picture_request(r1, r2)
        sp = req.system_prompt.lower()
        for lvl in ["minor", "moderate", "strong", "decisive"]:
            assert lvl in sp


# ===========================================================================
# assess_ae_clinical_picture — happy paths (scenarios A/B/C/D)
# ===========================================================================


class TestAssessAEClinicalPicture:
    def test_scenario_a_compatible_no_findings(self, fake_client):
        """Scenario A: clinically compatible wording → no findings."""
        fake_client.seed_response(
            AEClinicalPictureVerdict(findings=[], reasoning="all compatible")
        )

        r1 = _report_with_aes("R1", ["headache"])
        r2 = _report_with_aes("R2", ["cephalgia"], narrative="some text")
        result = assess_ae_clinical_picture(fake_client, "P1", r1, r2)

        assert result.status == AEClinicalPictureStatus.ASSESSED
        assert result.reason == ""
        assert result.counts.n_minor == 0
        assert result.counts.n_moderate == 0
        assert result.counts.n_strong == 0
        assert result.counts.n_decisive == 0
        assert result.findings == []
        assert result.reasoning == "all compatible"

    def test_scenario_b_broader_compatible_one_minor(self, fake_client):
        """Scenario B: broader-but-compatible → one minor finding."""
        fake_client.seed_response(
            AEClinicalPictureVerdict(
                findings=[
                    AEInconsistencyFindingLLM(
                        kind=AEInconsistencyKind.UNMATCHED_CLUSTER,
                        severity=InconsistencySeverity.MINOR,
                        rationale="extra peripheral term",
                        ae_terms=["dry mouth"],
                    )
                ]
            )
        )

        r1 = _report_with_aes("R1", ["headache"])
        r2 = _report_with_aes("R2", ["headache", "dry mouth"], narrative="x")
        result = assess_ae_clinical_picture(fake_client, "P1", r1, r2)

        assert result.status == AEClinicalPictureStatus.ASSESSED
        assert result.counts.n_minor == 1
        assert result.counts.n_moderate == 0
        assert len(result.findings) == 1
        finding = result.findings[0]
        assert finding.severity == InconsistencySeverity.MINOR
        assert finding.source == SOURCE_LABEL
        assert finding.kind == AEInconsistencyKind.UNMATCHED_CLUSTER.value
        assert finding.terms == ["dry mouth"]
        assert finding.suppressed is False

    def test_scenario_c_two_moderate_clusters_no_suppression(self, fake_client):
        """Scenario C: two moderate clusters on shared core — both kept."""
        fake_client.seed_response(
            AEClinicalPictureVerdict(
                findings=[
                    AEInconsistencyFindingLLM(
                        kind=AEInconsistencyKind.UNMATCHED_CLUSTER,
                        severity=InconsistencySeverity.MODERATE,
                        rationale="cutaneous syndrome only on R1",
                        ae_terms=["rash", "pruritus"],
                        cluster_label="cutaneous syndrome",
                    ),
                    AEInconsistencyFindingLLM(
                        kind=AEInconsistencyKind.UNMATCHED_CLUSTER,
                        severity=InconsistencySeverity.MODERATE,
                        rationale="hepatic injury only on R2",
                        ae_terms=["jaundice"],
                        cluster_label="hepatic injury",
                    ),
                ]
            )
        )

        r1 = _report_with_aes("R1", ["nausea", "rash", "pruritus"])
        r2 = _report_with_aes("R2", ["nausea", "jaundice"], narrative="x")
        result = assess_ae_clinical_picture(fake_client, "P1", r1, r2)

        assert result.counts.n_moderate == 2
        assert result.counts.n_decisive == 0
        kept = [f for f in result.findings if not f.suppressed]
        assert len(kept) == 2
        # cluster_label appears in feature key
        assert any("cutaneous syndrome" in f.feature for f in kept)
        assert any("hepatic injury" in f.feature for f in kept)

    def test_scenario_d_decisive_global_suppresses_subordinates(self, fake_client):
        """Scenario D: decisive global mismatch suppresses other findings."""
        fake_client.seed_response(
            AEClinicalPictureVerdict(
                findings=[
                    AEInconsistencyFindingLLM(
                        kind=AEInconsistencyKind.GLOBAL_PICTURE_MISMATCH,
                        severity=InconsistencySeverity.DECISIVE,
                        rationale="completely different pictures",
                        ae_terms=["headache", "myocardial infarction"],
                    ),
                    AEInconsistencyFindingLLM(
                        kind=AEInconsistencyKind.UNMATCHED_CLUSTER,
                        severity=InconsistencySeverity.MODERATE,
                        rationale="extra cluster",
                        ae_terms=["myocardial infarction"],
                    ),
                    AEInconsistencyFindingLLM(
                        kind=AEInconsistencyKind.MATCHED_CHARACTERIZATION,
                        severity=InconsistencySeverity.MINOR,
                        rationale="characterisation difference",
                        ae_terms=["headache"],
                    ),
                ]
            )
        )

        r1 = _report_with_aes("R1", ["headache"])
        r2 = _report_with_aes(
            "R2", ["myocardial infarction"], narrative="text"
        )
        result = assess_ae_clinical_picture(fake_client, "P1", r1, r2)

        # Counts: only the decisive global is kept
        assert result.counts.n_decisive == 1
        assert result.counts.n_minor == 0
        assert result.counts.n_moderate == 0
        assert result.counts.n_strong == 0

        # All three findings are still in the master list (transparency)
        assert len(result.findings) == 3
        kept = [f for f in result.findings if not f.suppressed]
        suppressed = [f for f in result.findings if f.suppressed]
        assert len(kept) == 1
        assert len(suppressed) == 2
        for s in suppressed:
            assert s.suppressed_by == SUPPRESSED_BY_DECISIVE_GLOBAL

    def test_long_overlap_with_few_extras_one_minor(self, fake_client):
        """Long AE list with broad overlap + a few extras → one minor."""
        fake_client.seed_response(
            AEClinicalPictureVerdict(
                findings=[
                    AEInconsistencyFindingLLM(
                        kind=AEInconsistencyKind.UNMATCHED_CLUSTER,
                        severity=InconsistencySeverity.MINOR,
                        rationale="few peripheral extras, broad overlap otherwise",
                        ae_terms=["hangnail"],
                    )
                ]
            )
        )

        shared = [f"event_{i}" for i in range(40)]
        r1 = _report_with_aes("R1", shared + ["hangnail"])
        r2 = _report_with_aes("R2", shared, narrative="text")
        result = assess_ae_clinical_picture(fake_client, "P1", r1, r2)
        # Schema-shape check; not testing LLM judgement
        assert result.counts.n_minor == 1
        assert result.counts.n_moderate == 0

    def test_handles_dict_parsed_payload(self, fake_client):
        """Some backends return dicts instead of model instances."""
        fake_client._queue.append(
            LLMResponse(
                parsed={
                    "findings": [
                        {
                            "kind": "unmatched_cluster",
                            "severity": "minor",
                            "rationale": "ok",
                            "ae_terms": ["x"],
                            "cluster_label": "",
                        }
                    ],
                    "reasoning": "from dict",
                },
                model="fake-model",
            )
        )

        r1 = _report_with_aes("R1", ["x"])
        r2 = _report_with_aes("R2", ["y"], narrative="x")
        result = assess_ae_clinical_picture(fake_client, "P1", r1, r2)
        assert result.status == AEClinicalPictureStatus.ASSESSED
        assert result.counts.n_minor == 1
        assert result.reasoning == "from dict"

    def test_raises_on_no_parsed_output(self, fake_client):
        fake_client.seed_raw("not parseable")
        r1 = _report_with_aes("R1", ["x"])
        r2 = _report_with_aes("R2", ["y"], narrative="text")
        with pytest.raises(ValueError, match="did not return structured output"):
            assess_ae_clinical_picture(fake_client, "P1", r1, r2)

    def test_error_message_includes_pair_id(self, fake_client):
        fake_client.seed_raw("bad")
        r1 = _report_with_aes("R1", ["x"])
        r2 = _report_with_aes("R2", ["y"], narrative="text")
        with pytest.raises(ValueError, match="R001__R002"):
            assess_ae_clinical_picture(fake_client, "R001__R002", r1, r2)

    def test_propagates_client_error(self, fake_client):
        fake_client.seed_error(ConnectionError("offline"))
        r1 = _report_with_aes("R1", ["x"])
        r2 = _report_with_aes("R2", ["y"], narrative="text")
        with pytest.raises(ConnectionError, match="offline"):
            assess_ae_clinical_picture(fake_client, "P1", r1, r2)


# ===========================================================================
# Trivially-matched short-circuit
# ===========================================================================


class TestTriviallyMatchedShortcut:
    def test_equal_pt_sets_and_empty_narratives_skips_llm(self, fake_client):
        r1 = _report_with_aes("R1", ["headache", "nausea"])
        r2 = _report_with_aes("R2", ["headache", "nausea"])
        result = assess_ae_clinical_picture(fake_client, "P1", r1, r2)

        assert result.status == AEClinicalPictureStatus.NOT_ASSESSED
        assert result.reason == "trivially_matched"
        assert result.findings == []
        assert fake_client.requests == []

    def test_pt_sets_normalised_for_match(self, fake_client):
        r1 = _report_with_aes("R1", ["Headache", "  NAUSEA "])
        r2 = _report_with_aes("R2", ["headache", "nausea"])
        result = assess_ae_clinical_picture(fake_client, "P1", r1, r2)
        assert result.status == AEClinicalPictureStatus.NOT_ASSESSED
        assert result.reason == "trivially_matched"
        assert fake_client.requests == []

    def test_narrative_present_disables_shortcut(self, fake_client):
        fake_client.seed_response(
            AEClinicalPictureVerdict(findings=[])
        )
        r1 = _report_with_aes("R1", ["headache"], narrative="Some narrative.")
        r2 = _report_with_aes("R2", ["headache"])
        result = assess_ae_clinical_picture(fake_client, "P1", r1, r2)
        assert result.status == AEClinicalPictureStatus.ASSESSED
        assert len(fake_client.requests) == 1

    def test_unequal_pt_sets_disables_shortcut(self, fake_client):
        fake_client.seed_response(
            AEClinicalPictureVerdict(findings=[])
        )
        r1 = _report_with_aes("R1", ["headache"])
        r2 = _report_with_aes("R2", ["nausea"])
        result = assess_ae_clinical_picture(fake_client, "P1", r1, r2)
        assert result.status == AEClinicalPictureStatus.ASSESSED
        assert len(fake_client.requests) == 1

    def test_both_empty_lists_and_empty_narratives_is_trivially_matched(
        self, fake_client
    ):
        r1 = _report_with_aes("R1", [])
        r2 = _report_with_aes("R2", [])
        result = assess_ae_clinical_picture(fake_client, "P1", r1, r2)
        assert result.status == AEClinicalPictureStatus.NOT_ASSESSED
        assert result.reason == "trivially_matched"
        assert fake_client.requests == []


# ===========================================================================
# collapse_subordinate_findings (pure function)
# ===========================================================================


class TestCollapseSubordinateFindings:
    def test_no_decisive_global_keeps_all(self):
        findings = [
            AEInconsistencyFindingLLM(
                kind=AEInconsistencyKind.UNMATCHED_CLUSTER,
                severity=InconsistencySeverity.MODERATE,
            ),
            AEInconsistencyFindingLLM(
                kind=AEInconsistencyKind.MATCHED_CHARACTERIZATION,
                severity=InconsistencySeverity.MINOR,
            ),
        ]
        kept, suppressed = collapse_subordinate_findings(findings)
        assert kept == findings
        assert suppressed == []

    def test_decisive_global_present_suppresses_others(self):
        global_finding = AEInconsistencyFindingLLM(
            kind=AEInconsistencyKind.GLOBAL_PICTURE_MISMATCH,
            severity=InconsistencySeverity.DECISIVE,
        )
        sub1 = AEInconsistencyFindingLLM(
            kind=AEInconsistencyKind.UNMATCHED_CLUSTER,
            severity=InconsistencySeverity.MODERATE,
        )
        sub2 = AEInconsistencyFindingLLM(
            kind=AEInconsistencyKind.MATCHED_CHARACTERIZATION,
            severity=InconsistencySeverity.MINOR,
        )
        kept, suppressed = collapse_subordinate_findings(
            [sub1, global_finding, sub2]
        )
        assert kept == [global_finding]
        assert suppressed == [sub1, sub2]

    def test_only_first_decisive_global_kept(self):
        g1 = AEInconsistencyFindingLLM(
            kind=AEInconsistencyKind.GLOBAL_PICTURE_MISMATCH,
            severity=InconsistencySeverity.DECISIVE,
            rationale="first",
        )
        g2 = AEInconsistencyFindingLLM(
            kind=AEInconsistencyKind.GLOBAL_PICTURE_MISMATCH,
            severity=InconsistencySeverity.DECISIVE,
            rationale="second",
        )
        kept, suppressed = collapse_subordinate_findings([g1, g2])
        assert kept == [g1]
        assert suppressed == [g2]

    def test_non_decisive_global_does_not_suppress(self):
        g = AEInconsistencyFindingLLM(
            kind=AEInconsistencyKind.GLOBAL_PICTURE_MISMATCH,
            severity=InconsistencySeverity.STRONG,  # not decisive
        )
        sub = AEInconsistencyFindingLLM(
            kind=AEInconsistencyKind.UNMATCHED_CLUSTER,
            severity=InconsistencySeverity.MODERATE,
        )
        kept, suppressed = collapse_subordinate_findings([g, sub])
        assert kept == [g, sub]
        assert suppressed == []

    def test_empty_input(self):
        kept, suppressed = collapse_subordinate_findings([])
        assert kept == []
        assert suppressed == []


# ===========================================================================
# Pipeline integration — generate_pair_features (no LLM client)
# ===========================================================================


class TestGeneratePairFeaturesNoAEResult:
    """When no AEClinicalPictureResult is passed, defaults are NOT_ASSESSED."""

    def test_defaults_to_not_assessed_with_no_llm_client_reason(
        self, report_a, report_b
    ):
        stats = FrequencyStats(
            N_total=100_000, N_drugs=48, N_AEs=200, N_drugs_AEs=12
        )
        features = generate_pair_features(
            r1=report_a,
            r2=report_b,
            stats=stats,
            narrative_consistency=NarrativeConsistency.NEUTRAL,
            config=PipelineConfig(),
        )

        assert (
            features.ae_clinical_picture_status
            == AEClinicalPictureStatus.NOT_ASSESSED
        )
        assert features.ae_clinical_picture_reason == "no_llm_client"
        assert features.ae_clinical_picture_counts.n_minor == 0
        assert features.ae_clinical_picture_counts.n_moderate == 0
        assert features.ae_clinical_picture_counts.n_strong == 0
        assert features.ae_clinical_picture_counts.n_decisive == 0
        # No AE findings appear in the master inconsistency list
        ae_entries = [
            i for i in features.inconsistencies if i.source == SOURCE_LABEL
        ]
        assert ae_entries == []


# ===========================================================================
# Pipeline integration — feature merging with AE results
# ===========================================================================


class TestPipelineFeatureMerge:
    def _stats(self) -> FrequencyStats:
        return FrequencyStats(
            N_total=100_000, N_drugs=48, N_AEs=200, N_drugs_AEs=12
        )

    def _make_ae_result(self, fake_client, verdict):
        fake_client.seed_response(verdict)

    def test_decisive_global_merges_one_decisive_count(
        self, report_a, report_b, fake_client
    ):
        """Decisive global mismatch contributes 1 decisive to merged counts."""
        from llm_dup_dev.llm.tasks.ae_clinical_picture import (
            assess_ae_clinical_picture,
        )

        fake_client.seed_response(
            AEClinicalPictureVerdict(
                findings=[
                    AEInconsistencyFindingLLM(
                        kind=AEInconsistencyKind.GLOBAL_PICTURE_MISMATCH,
                        severity=InconsistencySeverity.DECISIVE,
                        rationale="different pictures",
                    ),
                    AEInconsistencyFindingLLM(
                        kind=AEInconsistencyKind.UNMATCHED_CLUSTER,
                        severity=InconsistencySeverity.MODERATE,
                        rationale="extra",
                    ),
                ]
            )
        )
        ae_result = assess_ae_clinical_picture(
            fake_client, "P1", report_a, report_b
        )

        features = generate_pair_features(
            r1=report_a,
            r2=report_b,
            stats=self._stats(),
            narrative_consistency=NarrativeConsistency.NEUTRAL,
            ae_clinical_picture=ae_result,
        )

        # Merged counts include the decisive but not the suppressed moderate
        assert features.inconsistency_counts.decisive >= 1
        # The moderate finding is in the master list but suppressed
        ae_entries = [
            i for i in features.inconsistencies if i.source == SOURCE_LABEL
        ]
        assert len(ae_entries) == 2
        suppressed_moderates = [
            i
            for i in ae_entries
            if i.suppressed and i.severity == InconsistencySeverity.MODERATE
        ]
        assert len(suppressed_moderates) == 1
        # Flattened counts on PairFeatures (post-dedup)
        assert features.ae_clinical_picture_counts.n_decisive == 1
        assert features.ae_clinical_picture_counts.n_moderate == 0

    def test_two_moderates_both_contribute_to_merged_counts(
        self, report_a, report_b, fake_client
    ):
        from llm_dup_dev.llm.tasks.ae_clinical_picture import (
            assess_ae_clinical_picture,
        )

        fake_client.seed_response(
            AEClinicalPictureVerdict(
                findings=[
                    AEInconsistencyFindingLLM(
                        kind=AEInconsistencyKind.UNMATCHED_CLUSTER,
                        severity=InconsistencySeverity.MODERATE,
                    ),
                    AEInconsistencyFindingLLM(
                        kind=AEInconsistencyKind.UNMATCHED_CLUSTER,
                        severity=InconsistencySeverity.MODERATE,
                    ),
                ]
            )
        )
        ae_result = assess_ae_clinical_picture(
            fake_client, "P1", report_a, report_b
        )

        # Baseline (no AE result) merged-moderate count
        baseline = generate_pair_features(
            r1=report_a,
            r2=report_b,
            stats=self._stats(),
            narrative_consistency=NarrativeConsistency.NEUTRAL,
        )
        baseline_moderate = baseline.inconsistency_counts.moderate

        features = generate_pair_features(
            r1=report_a,
            r2=report_b,
            stats=self._stats(),
            narrative_consistency=NarrativeConsistency.NEUTRAL,
            ae_clinical_picture=ae_result,
        )

        assert (
            features.inconsistency_counts.moderate == baseline_moderate + 2
        )
        assert features.ae_clinical_picture_counts.n_moderate == 2


# ===========================================================================
# Pipeline integration — through PipelineRunner (no LLM client)
# ===========================================================================


class TestRunnerNoLLMClient:
    def test_runner_without_llm_records_not_assessed(
        self, report_a, report_b
    ):
        loader = SyntheticReportLoader(
            {
                report_a.report_id: report_a,
                report_b.report_id: report_b,
            }
        )
        stats = SyntheticStatsProvider(
            default_N_total=5000,
            default_N_drugs=200,
            default_N_AEs=300,
            default_N_drugs_AEs=40,
        )
        runner = PipelineRunner(
            report_loader=loader, stats_provider=stats,
            ae_clinical_picture_mode="llm",  # opt out of the auto rule-based fallback
        )
        pair = ReportPair(
            pair_id="R001__R002",
            report_1_id="R001",
            report_2_id="R002",
        )

        batch = runner.run_batch([pair])
        assert batch.n_processed == 1
        result = batch.results[0]

        assert (
            result.features.ae_clinical_picture_status
            == AEClinicalPictureStatus.NOT_ASSESSED
        )
        assert (
            result.features.ae_clinical_picture_reason == "no_llm_client"
        )
        assert result.features.ae_clinical_picture_counts.n_minor == 0
        assert result.features.ae_clinical_picture_counts.n_decisive == 0
        # The PairResult also exposes the raw assessment result
        assert result.ae_clinical_picture is not None
        assert (
            result.ae_clinical_picture.status
            == AEClinicalPictureStatus.NOT_ASSESSED
        )
        assert result.ae_clinical_picture.reason == "no_llm_client"

    def test_runner_with_llm_error_records_llm_error(
        self, report_a, report_b
    ):
        """LLM exception → NOT_ASSESSED with reason 'llm_error', pair still processed."""
        loader = SyntheticReportLoader(
            {
                report_a.report_id: report_a,
                report_b.report_id: report_b,
            }
        )
        stats = SyntheticStatsProvider(
            default_N_total=5000,
            default_N_drugs=200,
            default_N_AEs=300,
            default_N_drugs_AEs=40,
        )
        # Two LLM calls per pair: narrative, then AE clinical-picture.
        # Seed both as errors so neither succeeds and the pair still processes.
        fake = FakeLLMClient()
        fake.seed_error(ConnectionError("narrative offline"))
        fake.seed_error(ConnectionError("ae offline"))

        runner = PipelineRunner(
            report_loader=loader,
            stats_provider=stats,
            llm_client=fake,
            ae_clinical_picture_mode="llm",  # opt out of the auto rule-based fallback
        )
        pair = ReportPair(
            pair_id="R001__R002",
            report_1_id="R001",
            report_2_id="R002",
        )
        batch = runner.run_batch([pair])

        assert batch.n_processed == 1
        result = batch.results[0]
        assert (
            result.features.ae_clinical_picture_status
            == AEClinicalPictureStatus.NOT_ASSESSED
        )
        assert result.features.ae_clinical_picture_reason == "llm_error"


# ===========================================================================
# AEClinicalPictureVerdict schema
# ===========================================================================


class TestAEClinicalPictureVerdict:
    def test_roundtrip_json(self):
        v = AEClinicalPictureVerdict(
            findings=[
                AEInconsistencyFindingLLM(
                    kind=AEInconsistencyKind.UNMATCHED_CLUSTER,
                    severity=InconsistencySeverity.MODERATE,
                    rationale="cluster",
                    ae_terms=["a", "b"],
                    cluster_label="foo",
                )
            ],
            reasoning="ok",
        )
        data = v.model_dump()
        v2 = AEClinicalPictureVerdict.model_validate(data)
        assert v2 == v

    def test_defaults(self):
        v = AEClinicalPictureVerdict()
        assert v.findings == []
        assert v.reasoning == ""

    def test_json_schema_generation(self):
        schema = AEClinicalPictureVerdict.model_json_schema()
        assert "findings" in schema["properties"]
        assert "reasoning" in schema["properties"]

    def test_all_kinds_accepted(self):
        for kind in AEInconsistencyKind:
            f = AEInconsistencyFindingLLM(
                kind=kind, severity=InconsistencySeverity.MINOR
            )
            assert f.kind == kind

    def test_all_severities_accepted(self):
        for sev in InconsistencySeverity:
            f = AEInconsistencyFindingLLM(
                kind=AEInconsistencyKind.UNMATCHED_CLUSTER,
                severity=sev,
            )
            assert f.severity == sev
