"""A deterministic, readable account of what vigiTwin decided and why.

This is not a clinical judgement and not an independent assessment. It is a
rendering of the stored structured evidence into sentences, built from
conditional templates, with no language model involved and no I/O. The same
inputs always produce the same text.

Three rules govern what it may say, each of which is a way of being wrong that
the structured data makes easy:

* **Only what the record supports.** The independence *reason* is stored, so it
  is always named; the threshold it was compared against is not, so it is
  quoted only when a run manifest supplies it. Defaults are never substituted
  — a run with custom thresholds would then be described with numbers that
  never applied to it.
* **A shared set either defined the anchor or contributed to the score, never
  both.** ``compute_probability`` excludes an anchor-defining set from the
  contributions, so reading the contributions is already enough to get this
  right; the summary does not need to filter, and cannot drift.
* **The chance-match probability is not a probability of duplication.** The
  word "probability" does not appear in the output, and neither do
  ``p_match``, ``p_joint`` or ``L_match``. A reader gets "matching score".
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from llm_dup_dev.models.classification import ClassificationResult
from llm_dup_dev.models.enums import (
    AnchorType,
    IndependenceAssessment,
    IndependenceReason,
    RelationshipAssessment,
)
from llm_dup_dev.models.explicit_link import ExplicitLinkVerdict
from llm_dup_dev.models.narrative_source_case import (
    SourceCaseVerdict,
    SourceIdentifierType,
)
from llm_dup_dev.models.pair_features import PairFeatures

from ..attribution_maps import SEVERITY_ORDER
from .models import EvidenceGap, EvidenceGapCode
from .settings import EvidenceRunSettings

_NUMBER_WORDS = (
    "no",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
)


def _count(n: int) -> str:
    """Spell small counts, digit larger ones, as ordinary prose does."""
    if 0 <= n < len(_NUMBER_WORDS):
        return _NUMBER_WORDS[n]
    return f"{n:,}"


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    return singular if n == 1 else (plural or f"{singular}s")


def _join(items: list[str]) -> str:
    """``"a"`` / ``"a and b"`` / ``"a, b and c"``."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def format_score(value: float) -> str:
    """Format a matching score; an exact zero reads as ``0``, not ``0.0``.

    Public because the at-a-glance panel prints the score as a figure beside
    the clause this module writes about it. Both must render it identically,
    which one shared formatter guarantees and two conventions would not.
    """
    if value == 0:
        return "0"
    return f"{value:.1f}"


#: Most decimals a penalty or threshold is shown to before giving up on
#: separating two near-identical values.
_MAX_DECIMALS = 4


def _penalty(value: float, decimals: int = 1) -> str:
    return f"{value:.{decimals}f}"


def separating_decimals(a: float, b: float) -> int:
    """Decimals needed to show *a* and *b* as different numbers.

    One decimal is the house style, but a score of ``3.999`` against a
    threshold of ``4.0`` would then render as "3.999... is below 4.0" with both
    sides printed as ``4.0`` — a sentence that contradicts itself and hides
    exactly the near-threshold decision a reader most needs to examine. So the
    precision grows until the two values read differently, or until further
    decimals stop being informative.
    """
    if a == b:
        return 1
    for decimals in range(1, _MAX_DECIMALS + 1):
        if f"{a:.{decimals}f}" != f"{b:.{decimals}f}":
            return decimals
    return _MAX_DECIMALS


class SummarySlot(str, Enum):
    """The fixed sentence positions, in their canonical order.

    Evidence before decision, matching before discrepancy, independence before
    same-case, label last — the order in which the assessment itself proceeds.
    """

    SHARED_PATTERN = "shared_pattern"
    ANCHOR = "anchor"
    ADDITIONAL = "additional"
    DIRECT = "direct"
    DISCREPANCIES = "discrepancies"
    DECISION = "decision"
    RELATIONSHIP = "relationship"


class SummarySentence(BaseModel):
    """One sentence, tagged with the slot it fills.

    Tagged rather than concatenated so that a table and a paragraph can render
    the same objects and cannot drift apart.
    """

    slot: SummarySlot
    text: str


class AssessmentSummary(BaseModel):
    """The generated account of one pair's assessment."""

    sentences: list[SummarySentence] = Field(default_factory=list)
    gaps: list[EvidenceGap] = Field(default_factory=list)

    def as_text(self) -> str:
        """One paragraph: every sentence, space-joined, in slot order."""
        return " ".join(s.text for s in self.sentences)

    def paragraphs(self, *, split_after: SummarySlot | None = None) -> list[str]:
        """The sentences as one paragraph, or two split after *split_after*."""
        if split_after is None:
            return [self.as_text()] if self.sentences else []
        first: list[str] = []
        second: list[str] = []
        target = first
        for sentence in self.sentences:
            target.append(sentence.text)
            if sentence.slot is split_after:
                target = second
        return [" ".join(part) for part in (first, second) if part]


# ---------------------------------------------------------------------------
# Evidence sentences
# ---------------------------------------------------------------------------

_ANCHOR_PATTERN_NAME = {
    AnchorType.DRUGS: "shared drug pattern",
    AnchorType.AES: "shared adverse event pattern",
    AnchorType.DRUGS_AES: "shared drug and adverse event pattern",
}

_CONTEXT_PHRASE = {
    "country_year": "the selected country-year context",
    "country": "the selected country context",
    "global": "the global context",
}


def _shared_pattern_sentence(features: PairFeatures) -> str:
    n_drugs = len(features.matching_drugs)
    n_aes = len(features.matching_AEs)
    if n_drugs == 0 and n_aes == 0:
        return "The reports share no drug or adverse event terms."
    return (
        f"The reports share {_count(n_drugs)} drug "
        f"{_plural(n_drugs, 'term')} and {_count(n_aes)} adverse event "
        f"{_plural(n_aes, 'term')}."
    )


def _anchor_sentence(features: PairFeatures) -> str:
    anchor = features.anchor
    context = _CONTEXT_PHRASE.get(
        features.reporting_context.level.value, "the selected context"
    )
    pattern = _ANCHOR_PATTERN_NAME.get(anchor.type, "shared clinical pattern")

    if not features.matching_drugs and not features.matching_AEs:
        return (
            "With no shared clinical pattern to narrow it, the anchor "
            f"population is the whole of {context}, "
            f"{anchor.N_anchor:,} reports."
        )
    if anchor.N_anchor <= 2:
        return (
            f"The {pattern} defines an anchor population of "
            f"{_count(anchor.N_anchor)} "
            f"{_plural(anchor.N_anchor, 'report')} in {context}."
        )
    if anchor.type is AnchorType.DRUGS_AES:
        # Both sets narrow the population together, so "this combination"
        # names it correctly and reads better than repeating both.
        subject = "This combination"
    else:
        # Only one set narrows it. "This combination" would imply the other
        # set contributed to the anchor when it did not.
        subject = f"The {pattern}"
    return (
        f"{subject} occurs in {anchor.N_anchor:,} reports in {context} and "
        "defines the anchor population."
    )


#: Reader-facing names for the contributing features, in the order they are
#: listed. Fixed rather than taken from the stored contribution order: the
#: order features happen to appear in is an artefact of the computation, and
#: letting it through would make the same assessment produce two different
#: sentences. Clinical dates are appended after these, as a count.
_CONTRIBUTION_NAMES: tuple[tuple[str, str], ...] = (
    ("drug_set", "the shared drug pattern"),
    ("ae_set", "the shared adverse event pattern"),
    ("patient_sex", "sex"),
    ("patient_age", "age"),
    ("patient_height", "height"),
    ("patient_weight", "weight"),
)


def _additional_sentence(features: PairFeatures) -> str:
    """Name the features that contributed to the score, beyond the anchor.

    Read straight off the contributions, which already exclude whichever set
    defined the anchor. Nothing here has to know about that exclusion, so the
    sentence cannot start claiming an anchor set also raised the score.
    """
    present: set[str] = set()
    n_dates = 0
    for fc in features.probability.feature_contributions:
        if fc.p_f <= 0:
            continue
        if fc.feature == "clinical_date":
            n_dates += 1
            continue
        present.add(fc.feature)
    named = [name for feature, name in _CONTRIBUTION_NAMES if feature in present]
    if n_dates:
        named.append(f"{_count(n_dates)} clinical {_plural(n_dates, 'date')}")
    if not named:
        return "No additional matching evidence was identified."
    return f"Additional matching evidence includes {_join(named)}."


_SHARED_SOURCE_VERDICTS = {
    SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT,
    SourceCaseVerdict.SAME_SOURCE_SAME_CASE,
    SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE,
    SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
}

#: What kind of source an identifier names, in the reader's words. The stored
#: values are field names — ``article_reference``, ``trial_registration`` —
#: and the summary used to splice them straight into a sentence, so a reader
#: met "(a shared trial_registration)" in otherwise plain prose.
_SOURCE_KIND_PHRASE: dict[SourceIdentifierType, str] = {
    SourceIdentifierType.DOI: "publication",
    SourceIdentifierType.PMID: "publication",
    SourceIdentifierType.TRIAL_REGISTRATION: "trial registration",
    SourceIdentifierType.LINKED_CASE: "linked-case reference",
    SourceIdentifierType.PROTOCOL_NUMBER: "protocol number",
    SourceIdentifierType.STUDY_NAME: "named study",
    SourceIdentifierType.ARTICLE_REFERENCE: "literature citation",
    SourceIdentifierType.UNKNOWN: "source reference",
}

_SOURCE_VERDICT_PHRASE = {
    SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT: (
        "both reports cite the same source, which describes a single patient"
    ),
    SourceCaseVerdict.SAME_SOURCE_SAME_CASE: (
        "both reports cite the same source and carry matching case identifiers "
        "within it"
    ),
    SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE: (
        "both reports cite the same source but carry different case "
        "identifiers within it, so they describe different individuals"
    ),
    SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE: (
        "both reports cite the same source, but their case identifiers cannot "
        "be compared"
    ),
    SourceCaseVerdict.MULTI_PATIENT_CONTEXT_NO_LINK: (
        "at least one report describes a multi-patient source, with no shared "
        "identifier linking the two"
    ),
}


def _direct_sentence(
    features: PairFeatures, run_settings: EvidenceRunSettings | None = None
) -> str | None:
    """Direct evidence of shared provenance, when the record carries any.

    A run that did not use explicit links contributes none of that evidence,
    whatever its artifacts happen to carry in the field. Reading it anyway
    produced sentences citing a declaration the run never looked at.
    """
    parts: list[str] = []
    link = features.explicit_link
    links_used = (
        run_settings is None or run_settings.explicit_link_enabled is not False
    )
    if (
        links_used
        and link is not None
        and link.verdict == ExplicitLinkVerdict.EXPLICIT_LINK
    ):
        parts.append(
            "one report names the other in its case-identifier fields, an "
            "explicit declaration by the sender that the two describe the same "
            "case"
        )
    assessment = features.source_case_assessment
    if assessment is not None:
        phrase = _SOURCE_VERDICT_PHRASE.get(assessment.verdict)
        if phrase is not None:
            if assessment.verdict in _SHARED_SOURCE_VERDICTS and (
                assessment.shared_source_identifier_type is not None
            ):
                kind = _SOURCE_KIND_PHRASE.get(
                    assessment.shared_source_identifier_type,
                    "source reference",
                )
                phrase = f"{phrase} (a shared {kind})"
            parts.append(phrase)
    if not parts:
        return None
    joined = _join(parts)
    return f"Beyond the clinical overlap, {joined}."


# ---------------------------------------------------------------------------
# Discrepancy sentence
# ---------------------------------------------------------------------------


def _domain(feature: str) -> str:
    if feature == "drug_set":
        return "drug"
    if feature.startswith("ae_clinical_picture"):
        return "adverse event"
    if feature.startswith("source_case_"):
        return "source and case"
    if feature.startswith("patient_") or feature in (
        "age",
        "sex",
        "height",
        "weight",
    ):
        return "demographic"
    return "other"


def _discrepancy_groups(features: PairFeatures) -> list[tuple[str, str, int]]:
    """``(domain, severity, count)`` for every retained *weighed* finding.

    Suppressed findings are excluded: they contributed nothing to the penalty,
    so counting them here would describe an assessment that was not made.
    Clinical-date findings are counted at their final, post-downgrade
    severity, which is the severity that was actually weighed.

    Source-derived findings are excluded for a related reason, where the
    source context settled the relationship. The sentence this feeds reads
    "vigiTwin identified one decisive source and case inconsistency", which
    places the verdict among the weighed inconsistencies — and the verdict
    pinned the relationship whatever the penalty came to. The preceding
    sentence already states the shared source as direct evidence, so leaving
    it out here loses nothing and stops the summary crediting a penalty that
    decided nothing.
    """
    counts: dict[tuple[str, str], int] = {}
    deferred = _source_context_decided(features)
    for inc in features.inconsistencies:
        if inc.suppressed:
            continue
        if deferred and inc.feature.startswith("source_case_"):
            continue
        key = (_domain(inc.feature), inc.severity.value)
        counts[key] = counts.get(key, 0) + 1
    for finding in features.clinical_date_findings:
        key = ("clinical date", finding.severity.value)
        counts[key] = counts.get(key, 0) + 1
    return sorted(
        ((domain, sev, n) for (domain, sev), n in counts.items()),
        key=lambda item: (-SEVERITY_ORDER.get(item[1], -1), item[0]),
    )


#: Verdicts that settle the relationship on their own. Mirrors the pinned
#: branches of ``classifier.decision_logic._relationship``.
_RELATIONSHIP_PINNING_VERDICTS = frozenset(
    {SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE}
)


def _source_context_decided(features: PairFeatures) -> bool:
    """Whether a definitive source verdict settled the relationship."""
    assessment = features.source_case_assessment
    return (
        assessment is not None
        and assessment.verdict in _RELATIONSHIP_PINNING_VERDICTS
    )


def _discrepancy_phrases(features: PairFeatures) -> list[str]:
    return [
        f"{_count(n)} {sev} {domain} {_plural(n, 'inconsistency', 'inconsistencies')}"
        for domain, sev, n in _discrepancy_groups(features)
    ]


# ---------------------------------------------------------------------------
# Decision sentences
# ---------------------------------------------------------------------------

_ZONE_NAME = {
    IndependenceReason.RARITY_COMPELLING: "compelling",
    IndependenceReason.RARITY_INTERMEDIATE: "intermediate",
    IndependenceReason.RARITY_LIMITED: "limited",
}

_FINAL_LABEL_PHRASE = {
    "Duplicates": "duplicates",
    "Otherwise_related": "related but not the same case",
    "Unrelated": "unrelated",
}


def independence_clause(
    features: PairFeatures,
    classification: ClassificationResult,
    run_settings: EvidenceRunSettings | None,
    *,
    score: float | None = None,
) -> str:
    """Why independence was or was not rejected, by the recorded reason.

    ``score`` overrides the stored ``L_match`` with the figure the page
    actually shows — the contributions and the anchor adjustment as they add
    up. The two differ only where the stored score sits at a bound: at the
    ceiling the sum is the larger number, at the floor the stored score is
    held at zero while the sum has gone negative. Quoting the sum is the
    page's choice everywhere, so the clause quotes it too rather than naming
    a second, smaller or larger figure beside the column it came from.

    This cannot contradict the decision. At the ceiling both figures sit far
    above any independence threshold; at the floor both sit at or below zero.
    Away from a bound they agree to within a rounding tolerance, or the
    builder records a gap saying they do not.
    """
    reason = classification.independence_reason
    rejected = (
        classification.independence_assessment
        is IndependenceAssessment.NON_INDEPENDENT
    )

    # No "not on the matching score" on either of these. The summary reaches
    # the sentence that quotes the score only on the rarity route, so on
    # these two the qualifier answers a question nothing has raised — and it
    # read, on a decision tile that quotes no score at all, exactly the same
    # way.
    if reason is IndependenceReason.EXPLICIT_LINK:
        return (
            "Independence was rejected because one report explicitly "
            "references the other"
        )
    if reason is IndependenceReason.SHARED_SOURCE:
        return "Independence was rejected on direct evidence of a shared source"

    shown = features.L_match if score is None else score
    score_text = format_score(shown)
    threshold = (
        run_settings.independence_threshold(reason.value if reason else None)
        if run_settings is not None
        else None
    )
    if not rejected:
        clause = (
            f"The matching score of {score_text} did not support rejection "
            "of independence"
        )
        if threshold is not None:
            decimals = separating_decimals(shown, threshold)
            clause += (
                f", which required more than {_penalty(threshold, decimals)}"
            )
        return clause

    if threshold is not None:
        # The comparison before the conclusion, which is the order a reader
        # checks it in: the figure, the bar it had to clear, then what that
        # established. The zone is named without a number of its own — it is
        # not what independence required, it selects the same-case tolerance,
        # and attaching the zone's boundary here said this pair had to clear
        # 3.0 when it had to clear 1.0.
        zone = _ZONE_NAME.get(reason) if reason else None
        decimals = separating_decimals(shown, threshold)
        clause = (
            f"The matching score of {score_text} exceeds the independence "
            f"threshold of {_penalty(threshold, decimals)} and supports "
            "non-independence"
        )
        if zone:
            clause += f", falling in the {zone} range"
        return clause
    if reason is None:
        return "vigiTwin assessed the reports as non-independent"
    return f"The matching score of {score_text} supports non-independence"


def same_case_clause(classification: ClassificationResult) -> str | None:
    """The inconsistency-penalty comparison, exactly as it was made.

    A ``None`` threshold means no comparison took place, which happens two
    ways: independence was not rejected so the question never arose, or the
    relationship was pinned by source context regardless of the penalty.
    Saying "below the threshold" in either case would invent a comparison.

    The threshold is named with the zone it belongs to. There is no single
    same-case threshold: the tolerance varies with how strongly independence
    was rejected, and "the same-case threshold of 4.0" read as though 4.0
    were the bar for every pair. A reader comparing two cases would then
    find two different "the" thresholds and no reason for either.
    """
    penalty = classification.penalty
    relationship = classification.relationship_assessment
    # Empty for a pair whose independence was decided on direct evidence
    # rather than on rarity: there is no zone to name, and the tolerance was
    # whatever that route uses.
    reason = classification.independence_reason
    zone = _ZONE_NAME.get(reason) if reason else None
    for_zone = f" for the {zone} range" if zone else ""

    if penalty is None:
        return None

    if penalty.threshold is None:
        if (
            classification.independence_assessment
            is IndependenceAssessment.INDEPENDENT
        ):
            return (
                "the relationship question was not reached, because "
                "independence was not rejected"
            )
        # "with no inconsistency-penalty comparison" restated the clause it
        # was attached to: being assessed on source context alone is what
        # having no penalty comparison means.
        return "the reports were assessed on source context alone"

    if relationship is RelationshipAssessment.ABSTAIN:
        decimals = separating_decimals(penalty.w_tot, penalty.threshold)
        return (
            f"the total inconsistency penalty of "
            f"{_penalty(penalty.w_tot, decimals)} reached the same-case "
            f"tolerance of {_penalty(penalty.threshold, decimals)}"
            f"{for_zone}, leaving the question unresolved"
        )

    comparison = "below" if penalty.satisfied else "at or above"
    decimals = separating_decimals(penalty.w_tot, penalty.threshold)
    return (
        f"the total inconsistency penalty of "
        f"{_penalty(penalty.w_tot, decimals)} is {comparison} the same-case "
        f"threshold of {_penalty(penalty.threshold, decimals)}{for_zone}"
    )


def _label_sentence(classification: ClassificationResult) -> str:
    label = classification.final_label
    phrase = _FINAL_LABEL_PHRASE.get(label)
    if phrase is None:
        return (
            "vigiTwin reached no classification: the evidence was "
            "insufficient to resolve whether the reports describe the same "
            "case."
        )
    return f"The reports were classified as {phrase}."


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_assessment_summary(
    features: PairFeatures,
    classification: ClassificationResult | None = None,
    *,
    run_settings: EvidenceRunSettings | None = None,
    score: float | None = None,
) -> AssessmentSummary:
    """Render one pair's assessment as a short readable account.

    The sentence order depends on the outcome, because the two outcomes read
    naturally in different orders. For a pair whose independence was rejected,
    the findings come before the decision that weighed them. For a pair that
    stayed independent, the decision comes first and the findings follow with
    the explicit note that they were never weighed — which is the fact a
    reader most needs, and the thing an undifferentiated template would bury.
    """
    sentences: list[SummarySentence] = []
    gaps: list[EvidenceGap] = []

    def add(slot: SummarySlot, text: str | None) -> None:
        if text:
            sentences.append(SummarySentence(slot=slot, text=text))

    add(SummarySlot.SHARED_PATTERN, _shared_pattern_sentence(features))
    add(SummarySlot.ANCHOR, _anchor_sentence(features))
    add(SummarySlot.ADDITIONAL, _additional_sentence(features))
    add(SummarySlot.DIRECT, _direct_sentence(features, run_settings))

    if classification is None:
        gaps.append(
            EvidenceGap(
                code=EvidenceGapCode.NO_CLASSIFICATION,
                message=(
                    "No classification was supplied for this pair, so the "
                    "summary describes the evidence without an outcome."
                ),
            )
        )
        phrases = _discrepancy_phrases(features)
        if phrases:
            add(
                SummarySlot.DISCREPANCIES,
                f"vigiTwin identified {_join(phrases)}.",
            )
        return AssessmentSummary(sentences=sentences, gaps=gaps)

    if run_settings is None or run_settings.score_ceiling is None:
        gaps.append(
            EvidenceGap(
                code=EvidenceGapCode.RUN_SETTINGS_MISSING,
                message=(
                    "The decision thresholds and numerical settings for this "
                    "run were not recorded, so this summary names the evidence "
                    "zone rather than quoting the thresholds it was compared "
                    "against."
                ),
            )
        )

    phrases = _discrepancy_phrases(features)
    independence = independence_clause(
        features, classification, run_settings, score=score
    )
    same_case = same_case_clause(classification)
    rejected = (
        classification.independence_assessment
        is IndependenceAssessment.NON_INDEPENDENT
    )

    if rejected:
        add(
            SummarySlot.DISCREPANCIES,
            f"vigiTwin identified {_join(phrases)}."
            if phrases
            else "vigiTwin identified no inconsistencies.",
        )
        decision = independence
        if same_case:
            decision = f"{decision}, and {same_case}"
        add(SummarySlot.DECISION, f"{decision}.")
        add(SummarySlot.RELATIONSHIP, _label_sentence(classification))
        return AssessmentSummary(sentences=sentences, gaps=gaps)

    # Independent: the decision first, then the findings that were recorded
    # but never weighed against it.
    label = _FINAL_LABEL_PHRASE.get(classification.final_label, "unrelated")
    add(
        SummarySlot.DECISION,
        f"{independence}, and the reports were classified as {label}.",
    )
    # Say *why* no same-case comparison happened. "No same-case decision was
    # reached" reads as though the relationship assessment ran and stalled;
    # what actually happened is that it was never reached, because the
    # question only arises once independence is rejected.
    if phrases:
        add(
            SummarySlot.RELATIONSHIP,
            f"{_join(phrases).capitalize()} "
            + ("were" if _total_findings(features) != 1 else "was")
            + " identified, but not weighed against the same-case tolerance: "
            "that comparison is only reached once independence is rejected.",
        )
    else:
        add(
            SummarySlot.RELATIONSHIP,
            "No inconsistencies were identified, and the relationship "
            "question was not reached, because independence was not rejected.",
        )
    return AssessmentSummary(sentences=sentences, gaps=gaps)


def _total_findings(features: PairFeatures) -> int:
    return sum(n for _, _, n in _discrepancy_groups(features))


__all__ = [
    "AssessmentSummary",
    "SummarySentence",
    "SummarySlot",
    "build_assessment_summary",
    "format_score",
    "separating_decimals",
    "independence_clause",
    "same_case_clause",
]
