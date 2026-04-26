"""Tests for the deterministic AE clinical-picture assessor.

Covers the nine scenarios listed in the spec:
1. Exact PT match
2. Same HLT, different PT
3. Same HLGT only
4. Same SOC only
5. No shared SOC
6. Subset / additional cluster
7. Shared HLT prevents unmatched counting
8. Investigations SOC exclusion
9. Missing hierarchy
"""

from __future__ import annotations

from llm_dup_dev.feature_generation.ae_clinical_picture_rules import (
    assess_ae_clinical_picture_rule_based,
)
from llm_dup_dev.models.enums import (
    AEClinicalPictureStatus,
    InconsistencySeverity,
)
from llm_dup_dev.models.pair_features import GlobalOverlapLevel
from llm_dup_dev.models.report import AdverseEvent, Report


def _ae(pt: str, hlt: str | None = None, hlgt: str | None = None, soc: str | None = None) -> AdverseEvent:
    return AdverseEvent(
        pt_name=pt,
        sources=["structured"],
        primary_hlt_name=hlt,
        primary_hlgt_name=hlgt,
        primary_soc_name=soc,
    )


def _report(rid: str, aes: list[AdverseEvent]) -> Report:
    return Report(report_id=rid, adverse_events=aes)


# --------------------------------------------------------------------
# 1. Exact PT match
# --------------------------------------------------------------------
def test_exact_pt_match():
    r1 = _report("R1", [_ae("arrhythmia", hlt="cardiac arrhythmias",
                              hlgt="cardiac arrhythmias",
                              soc="cardiac disorders")])
    r2 = _report("R2", [_ae("arrhythmia", hlt="cardiac arrhythmias",
                              hlgt="cardiac arrhythmias",
                              soc="cardiac disorders")])

    result = assess_ae_clinical_picture_rule_based(r1, r2)
    assert result.status == AEClinicalPictureStatus.ASSESSED
    assert result.method == "rule_based"
    d = result.rule_based_details
    assert d.step1_overlap_level == GlobalOverlapLevel.PT
    assert d.step1_severity is None
    assert d.step2_r1_to_r2_severity is None
    assert d.step2_r2_to_r1_severity is None
    assert result.counts.n_minor == 0
    assert result.counts.n_moderate == 0
    assert result.counts.n_strong == 0
    assert result.counts.n_decisive == 0


# --------------------------------------------------------------------
# 2. Same HLT but different PT
# --------------------------------------------------------------------
def test_same_hlt_different_pt():
    # Both PTs share the HLT "rashes, eruptions and exanthems NEC"
    r1 = _report("R1", [_ae("rash", hlt="rashes eruptions and exanthems nec",
                              hlgt="epidermal and dermal conditions",
                              soc="skin and subcutaneous tissue disorders")])
    r2 = _report("R2", [_ae("erythema", hlt="rashes eruptions and exanthems nec",
                              hlgt="epidermal and dermal conditions",
                              soc="skin and subcutaneous tissue disorders")])

    result = assess_ae_clinical_picture_rule_based(r1, r2)
    d = result.rule_based_details
    assert d.step1_overlap_level == GlobalOverlapLevel.HLT
    assert d.step1_severity == InconsistencySeverity.MINOR
    # Step 2: HLT shared in target → counted as M, not U.
    assert d.step2_r1_to_r2_u == 0
    assert d.step2_r1_to_r2_m == 1
    assert d.step2_r1_to_r2_severity is None
    assert d.step2_r2_to_r1_u == 0
    assert d.step2_r2_to_r1_m == 1
    assert d.step2_r2_to_r1_severity is None


# --------------------------------------------------------------------
# 3. Same HLGT only
# --------------------------------------------------------------------
def test_same_hlgt_only():
    r1 = _report("R1", [_ae("headache", hlt="headaches nec",
                              hlgt="headaches",
                              soc="nervous system disorders")])
    r2 = _report("R2", [_ae("migraine", hlt="migraine headaches",
                              hlgt="headaches",
                              soc="nervous system disorders")])

    result = assess_ae_clinical_picture_rule_based(r1, r2)
    d = result.rule_based_details
    assert d.step1_overlap_level == GlobalOverlapLevel.HLGT
    assert d.step1_severity == InconsistencySeverity.MODERATE


# --------------------------------------------------------------------
# 4. Same SOC only
# --------------------------------------------------------------------
def test_same_soc_only():
    r1 = _report("R1", [_ae("ataxia", hlt="coordination and balance disturbances",
                              hlgt="neurological disorders nec",
                              soc="nervous system disorders")])
    r2 = _report("R2", [_ae("seizure", hlt="seizures and seizure disorders nec",
                              hlgt="seizures",
                              soc="nervous system disorders")])

    result = assess_ae_clinical_picture_rule_based(r1, r2)
    d = result.rule_based_details
    assert d.step1_overlap_level == GlobalOverlapLevel.SOC
    assert d.step1_severity == InconsistencySeverity.STRONG


# --------------------------------------------------------------------
# 5. No shared SOC
# --------------------------------------------------------------------
def test_no_shared_soc():
    r1 = _report("R1", [_ae("arrhythmia", hlt="cardiac arrhythmias",
                              hlgt="cardiac arrhythmias",
                              soc="cardiac disorders")])
    r2 = _report("R2", [_ae("rash", hlt="rashes eruptions and exanthems nec",
                              hlgt="epidermal and dermal conditions",
                              soc="skin and subcutaneous tissue disorders")])

    result = assess_ae_clinical_picture_rule_based(r1, r2)
    d = result.rule_based_details
    assert d.step1_overlap_level == GlobalOverlapLevel.NONE
    assert d.step1_severity == InconsistencySeverity.DECISIVE
    assert result.counts.n_decisive == 1


# --------------------------------------------------------------------
# 6. Subset / additional cluster
# --------------------------------------------------------------------
def test_subset_additional_cluster():
    arrhythmia = _ae("arrhythmia", hlt="cardiac arrhythmias",
                       hlgt="cardiac arrhythmias",
                       soc="cardiac disorders")
    pallor = _ae("pallor", hlt="appearance disorders",
                   hlgt="general system disorders nec",
                   soc="general disorders and administration site conditions")
    loc = _ae("loss of consciousness", hlt="disturbances in consciousness nec",
                hlgt="neurological disorders nec",
                soc="nervous system disorders")
    mydriasis = _ae("mydriasis", hlt="ocular neuromuscular disorders",
                      hlgt="ocular sensory symptoms nec",
                      soc="eye disorders")
    incontinence = _ae("urinary incontinence", hlt="urinary incontinence",
                         hlgt="bladder disorders excl calculi",
                         soc="renal and urinary disorders")

    r1 = _report("R1", [arrhythmia])
    r2 = _report("R2", [arrhythmia, pallor, loc, mydriasis, incontinence])

    result = assess_ae_clinical_picture_rule_based(r1, r2)
    d = result.rule_based_details
    # Step 1: PT match (arrhythmia == arrhythmia) → no inconsistency.
    assert d.step1_overlap_level == GlobalOverlapLevel.PT
    assert d.step1_severity is None
    # R1 → R2: every R1 PT matches.
    assert d.step2_r1_to_r2_u == 0
    assert d.step2_r1_to_r2_severity is None
    # R2 → R1: arrhythmia matches; pallor / LoC / mydriasis / incontinence
    # have HLTs absent from R1 → 4 unmatched of 5 → moderate.
    assert d.step2_r2_to_r1_u == 4
    assert d.step2_r2_to_r1_m == 1
    assert d.step2_r2_to_r1_severity == InconsistencySeverity.MODERATE


# --------------------------------------------------------------------
# 7. Shared HLT prevents unmatched counting
# --------------------------------------------------------------------
def test_shared_hlt_prevents_unmatched_counting():
    # PT A in R1 and PT B in R2 both have the same HLT.
    a = _ae("pt_a", hlt="shared hlt", hlgt="hlgt_x", soc="soc_x")
    b = _ae("pt_b", hlt="shared hlt", hlgt="hlgt_x", soc="soc_x")
    r1 = _report("R1", [a])
    r2 = _report("R2", [b])

    result = assess_ae_clinical_picture_rule_based(r1, r2)
    d = result.rule_based_details
    # PTs differ; HLT shared → MINOR overlap, no unmatched burden.
    assert d.step1_overlap_level == GlobalOverlapLevel.HLT
    assert d.step2_r1_to_r2_u == 0
    assert d.step2_r1_to_r2_m == 1
    assert d.step2_r1_to_r2_severity is None


# --------------------------------------------------------------------
# 8. Investigations SOC exclusion
# --------------------------------------------------------------------
def test_investigations_soc_exclusion():
    alt = _ae("alanine aminotransferase increased", hlt="liver function analyses",
                hlgt="hepatobiliary investigations",
                soc="investigations")
    ast = _ae("aspartate aminotransferase increased", hlt="liver function analyses",
                hlgt="hepatobiliary investigations",
                soc="investigations")
    hepatitis = _ae("hepatitis", hlt="hepatic and hepatobiliary disorders",
                      hlgt="hepatic disorders",
                      soc="hepatobiliary disorders")

    r1 = _report("R1", [alt, ast])
    r2 = _report("R2", [hepatitis])

    result = assess_ae_clinical_picture_rule_based(r1, r2)
    d = result.rule_based_details

    # Step 1 includes Investigations: HLGT differs, SOCs differ → DECISIVE.
    # (The Investigations SOC entries don't share any hierarchy with the
    # hepatobiliary SOC entry here, so Step 1 is decisive — confirming
    # the spec contract that Step 1 still considers Investigations PTs.)
    assert d.step1_overlap_level == GlobalOverlapLevel.NONE

    # Step 2 R1 → R2: both source PTs are Investigations, filtered out
    # before counting → U = M = 0, severity = None.
    assert d.step2_r1_to_r2_u == 0
    assert d.step2_r1_to_r2_m == 0
    assert d.step2_r1_to_r2_severity is None
    assert d.investigations_excluded_step2 is True

    # Step 2 R2 → R1: target side has only Investigations PTs (filtered);
    # source hepatitis PT (not Investigations) has HLT not present in
    # the empty target HLT set → counts as 1 unmatched → MINOR.
    assert d.step2_r2_to_r1_u == 1
    assert d.step2_r2_to_r1_severity == InconsistencySeverity.MINOR


# --------------------------------------------------------------------
# 9. Missing hierarchy
# --------------------------------------------------------------------
def test_missing_hierarchy():
    r1 = _report("R1", [_ae("pt_alpha"), _ae("pt_beta")])
    r2 = _report("R2", [_ae("pt_gamma")])

    result = assess_ae_clinical_picture_rule_based(r1, r2)
    assert result.status == AEClinicalPictureStatus.NOT_ASSESSED
    assert result.reason == "missing_hierarchy"
    assert result.method == "rule_based"
    assert len(result.findings) == 0
    assert result.counts.n_minor == 0
    # Warnings cite the offending report:pt entries.
    warnings = result.rule_based_details.missing_hierarchy_warnings
    assert any("R1:pt_alpha" in w for w in warnings)
    assert any("R2:pt_gamma" in w for w in warnings)


# --------------------------------------------------------------------
# Bonus: fallback_from_llm flag flips the reason field.
# --------------------------------------------------------------------
def test_fallback_from_llm_reason():
    r1 = _report("R1", [_ae("arrhythmia", hlt="cardiac arrhythmias",
                              hlgt="cardiac arrhythmias",
                              soc="cardiac disorders")])
    r2 = _report("R2", [_ae("arrhythmia", hlt="cardiac arrhythmias",
                              hlgt="cardiac arrhythmias",
                              soc="cardiac disorders")])

    direct = assess_ae_clinical_picture_rule_based(r1, r2)
    fallback = assess_ae_clinical_picture_rule_based(r1, r2, fallback_from_llm=True)
    assert direct.reason == "rule_based"
    assert fallback.reason == "llm_fallback_to_rule"
    assert fallback.method == "rule_based"
