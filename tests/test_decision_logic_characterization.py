"""Characterization (golden) test for ``classifier.decision_logic.classify``.

Pins the full decision surface of ``classify`` across a grid of inputs so
that restructurings of the decision logic can be verified to preserve
behavior. The golden file records, per grid cell: ``final_label``, the
independence assessment, the independence reason, the relationship
assessment, ``w_tot``, and the threshold the score was compared against.
Explanation strings are deliberately excluded — they are presentation,
not decision.

The grid is defined in this module; the golden file stores only the
outputs, in grid order (JSONL: one metadata line, then one row per cell).
To regenerate after an *intended* behavior change::

    REGENERATE_GOLDEN=1 uv run pytest tests/test_decision_logic_characterization.py

Grid values straddle every decision boundary of both threshold variants:
``L_match`` around the limited/intermediate/compelling bounds (strict >,
so exact-boundary values fall to the lower zone), inconsistency-count
tuples whose ``w_tot`` lands below / at / above each zone tolerance
(strict <, so at-threshold fails the zone), and ``L_patient`` around
``lpatient_compelling`` for both variants.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from llm_dup_dev.classifier.config import DecisionThresholds
from llm_dup_dev.classifier.decision_logic import classify
from llm_dup_dev.models.enums import (
    AnchorType,
    ReportingContextLevel,
)
from llm_dup_dev.models.narrative_source_case import (
    SourceCaseAssessment,
    SourceCaseVerdict,
)
from llm_dup_dev.models.pair_features import (
    AnchorPopulation,
    InconsistencyCounts,
    PairFeatures,
    ProbabilityEstimate,
    ReportingContext,
)

GOLDEN_PATH = Path(__file__).parent / "data" / "decision_logic_golden.jsonl"
GOLDEN_FORMAT = 1

# L_match values: default zone bounds are 1.0 / 2.0 / 3.0, the custom
# variant's are 1.5 / 2.5 / 4.0. Exact-boundary values (strict >) and
# just-above values for each, plus 0.0 well below everything.
L_MATCH_VALUES = (
    0.0, 1.0, 1.001, 1.5, 1.501, 2.0, 2.001, 2.5, 2.501, 3.0, 3.001, 4.001,
)

# (minor, moderate, strong, decisive) tuples. With the default weights
# (0.4 / 1.0 / 3.0 / 100) these produce w_tot values hitting each default
# zone tolerance (1.0 / 3.0 / 4.0) exactly and on both sides; with the
# custom weights (0.5 / 1.5 / 4.0 / 50) they likewise hit the custom
# tolerances (2.0 / 3.5 / 5.0).
COUNT_TUPLES = (
    (0, 0, 0, 0),
    (1, 0, 0, 0),
    (2, 0, 0, 0),
    (3, 0, 0, 0),
    (0, 1, 0, 0),
    (1, 1, 0, 0),
    (1, 2, 0, 0),
    (0, 2, 0, 0),
    (2, 0, 1, 0),
    (0, 3, 0, 0),
    (0, 4, 0, 0),
    (0, 0, 1, 0),
    (1, 0, 1, 0),
    (0, 1, 1, 0),
    (0, 0, 2, 0),
    (0, 0, 0, 1),
)

# None (no assessment) plus every verdict, advisory ones included.
VERDICTS = (None, *SourceCaseVerdict)

# Around lpatient_compelling: 2.0 (default; not >) with 2.001 just above,
# and 3.001 just above the custom variant's bar of 3.0.
L_PATIENT_VALUES = (0.0, 2.0, 2.001, 3.001)

THRESHOLD_VARIANTS = (
    ("default", DecisionThresholds()),
    (
        "custom",
        DecisionThresholds(
            lmatch_compelling=4.0,
            lmatch_intermediate=2.5,
            lmatch_limited=1.5,
            lpatient_compelling=3.0,
            w_minor=0.5,
            w_moderate=1.5,
            w_strong=4.0,
            w_decisive=50.0,
            w_tot_compelling_zone=5.0,
            w_tot_intermediate_zone=3.5,
            w_tot_limited_zone=2.0,
        ),
    ),
)


def _make_features(
    L_match: float,
    counts: tuple[int, int, int, int],
    verdict: SourceCaseVerdict | None,
    L_patient: float,
) -> PairFeatures:
    """Minimal PairFeatures; only the decision-relevant fields vary."""
    minor, moderate, strong, decisive = counts
    return PairFeatures(
        pair_id="golden",
        reporting_context=ReportingContext(level=ReportingContextLevel.GLOBAL),
        anchor=AnchorPopulation(
            N_drugs=100, N_AEs=100, N_drugs_AEs=50,
            type=AnchorType.DRUGS_AES, N_anchor=50,
        ),
        probability=ProbabilityEstimate(p_joint=1e-5, p_match=0.01),
        L_match=L_match,
        L_patient=L_patient,
        inconsistency_counts=InconsistencyCounts(
            minor=minor, moderate=moderate, strong=strong, decisive=decisive,
        ),
        source_case_assessment=(
            SourceCaseAssessment(verdict=verdict) if verdict is not None else None
        ),
    )


def iter_cells():
    """Yield every grid cell in the fixed golden order."""
    for variant_name, thresholds in THRESHOLD_VARIANTS:
        for L_match in L_MATCH_VALUES:
            for counts in COUNT_TUPLES:
                for verdict in VERDICTS:
                    for L_patient in L_PATIENT_VALUES:
                        yield variant_name, thresholds, L_match, counts, verdict, L_patient


# The golden file was generated before the relationship vocabulary rename
# (Duplicates / Otherwise_related → Same_case / Different_case). Translate
# current values back to the golden vocabulary so the committed golden
# stays byte-stable across the rename.
_GOLDEN_RELATIONSHIP = {
    "Same_case": "Duplicates",
    "Different_case": "Otherwise_related",
    "Abstain": "Abstain",
}


def _cell_row(thresholds: DecisionThresholds, cell) -> list:
    """Classify one cell and return its golden row."""
    _, _, L_match, counts, verdict, L_patient = cell
    result = classify(_make_features(L_match, counts, verdict, L_patient), thresholds)
    relationship = (
        _GOLDEN_RELATIONSHIP[result.relationship_assessment.value]
        if result.relationship_assessment
        else None
    )
    return [
        result.final_label,
        result.independence_assessment.value,
        result.independence_reason.value if result.independence_reason else None,
        relationship,
        round(result.penalty.w_tot, 6) if result.penalty else None,
        result.penalty.threshold if result.penalty else None,
    ]


def _cell_id(cell) -> str:
    variant_name, _, L_match, counts, verdict, L_patient = cell
    verdict_name = verdict.value if verdict is not None else "no_assessment"
    return (
        f"{variant_name} L_match={L_match} counts(mi,mo,st,de)={counts} "
        f"verdict={verdict_name} L_patient={L_patient}"
    )


def _adjust_for_multi_patient_reason(cell, expected: list) -> list:
    """Apply the one intended deviation from the pre-restructure golden.

    ``MULTI_PATIENT_CONTEXT_NO_LINK`` pairs above ``lmatch_limited``
    used to record ``independence_reason = shared_source`` even though
    no shared source identifier exists; independence rejection actually
    comes from the rarity evidence. The restructured decision logic
    records the rarity zone instead. Assert the golden held the old
    value and substitute the expected new one — everything else in the
    row must still match exactly.
    """
    _, thresholds, L_match, _, verdict, _ = cell
    if (
        verdict == SourceCaseVerdict.MULTI_PATIENT_CONTEXT_NO_LINK
        and L_match > thresholds.lmatch_limited
    ):
        assert expected[2] == "shared_source", (
            "golden drifted: multi-patient cells above lmatch_limited "
            "were recorded with reason shared_source before the restructure"
        )
        if L_match > thresholds.lmatch_compelling:
            new_reason = "rarity_compelling"
        elif L_match > thresholds.lmatch_intermediate:
            new_reason = "rarity_intermediate"
        else:
            new_reason = "rarity_limited"
        return [expected[0], expected[1], new_reason, *expected[3:]]
    return expected


def _regenerate() -> int:
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    rows = [_cell_row(cell[1], cell) for cell in iter_cells()]
    with GOLDEN_PATH.open("w") as fh:
        fh.write(json.dumps({"format": GOLDEN_FORMAT, "n_rows": len(rows)}) + "\n")
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return len(rows)


def test_classify_matches_golden():
    if os.environ.get("REGENERATE_GOLDEN"):
        n = _regenerate()
        raise AssertionError(
            f"Golden regenerated with {n} rows at {GOLDEN_PATH}; "
            f"rerun without REGENERATE_GOLDEN to compare."
        )

    with GOLDEN_PATH.open() as fh:
        meta = json.loads(fh.readline())
        golden_rows = [json.loads(line) for line in fh]
    assert meta["format"] == GOLDEN_FORMAT

    cells = list(iter_cells())
    assert len(cells) == meta["n_rows"] == len(golden_rows), (
        "Grid size differs from golden — the grid definition changed; "
        "regenerate deliberately if intended."
    )

    mismatches = []
    for cell, expected in zip(cells, golden_rows):
        expected = _adjust_for_multi_patient_reason(cell, expected)
        actual = _cell_row(cell[1], cell)
        if actual != expected:
            mismatches.append(f"{_cell_id(cell)}\n  expected {expected}\n  actual   {actual}")
            if len(mismatches) >= 10:
                break
    assert not mismatches, (
        "classify() deviates from the golden decision surface "
        f"({len(mismatches)}+ cells):\n" + "\n".join(mismatches)
    )
