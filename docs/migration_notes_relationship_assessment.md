# Migration notes: relationship-assessment restructure

**Audience:** consumers of serialized pipeline output — in particular the
secure-side deployment (`llm_dup_pv_secure`) and any tooling that reads
`ClassificationResult` JSON, the generated schemas, or archived batch exports.
This document describes the contract changes only; no secure-side code is
modified from this repository.

## Summary

The decision logic was restructured into two explicit sequential assessments
— independence, then relationship — matching the method specification
(`duplicate_adjudication_spec.md`). The internal relationship vocabulary was
renamed so it no longer reuses the user-facing final-label words.

**`ClassificationResult.final_label` is unchanged** — the values
`"Duplicates"`, `"Otherwise_related"`, `"Unrelated"`, and `"Abstain"` and
their semantics are identical. **Any consumer that depends only on
`final_label` needs no change.** That has always been the documented
contract; the changes below affect only consumers reading the raw internal
fields.

## Breaking changes to serialized `ClassificationResult`

1. **Field rename:** `duplicate_relationship` → `relationship_assessment`.
2. **Value renames** on that field: `"Duplicates"` → `"Same_case"`,
   `"Otherwise_related"` → `"Different_case"`. `"Abstain"` is unchanged, as
   is `null` for Independent pairs.
3. **Enum rename in schemas:** the `$defs` entry `DuplicateRelationship` is
   now `RelationshipAssessment` in `schemas/classification_result.schema.json`.
4. **Removed never-emitted enum members:** `IndependenceAssessment` no longer
   declares `"Abstain"`, and `IndependenceReason` no longer declares
   `"abstain"`. Neither value was ever produced by any code path; only
   schemas and defensive consumer code could reference them.
5. **`independence_reason` for multi-patient pairs:** pairs with the
   source-case verdict `multi_patient_context_no_link` and
   `L_match > lmatch_limited` previously recorded
   `independence_reason = "shared_source"`. They now record the rarity zone
   (`"rarity_compelling"` / `"rarity_intermediate"` / `"rarity_limited"`),
   which is what actually rejected independence — this verdict carries no
   shared source identifier. Final labels are unaffected.
6. **Explanation strings** were reworded ("Shared-source override (…)" →
   "Shared source (…)" / "Multi-patient context (…)"; internal label words
   updated). Explanations are display-only prose and were never a stable
   contract.

Unchanged: `pair_id`, `independence_assessment` values
(`"Non-independent"` / `"Independent"`), the `penalty` block,
`PairFeatures` and its schema (including `SourceCaseVerdict` values), the
frozen-extraction bundle formats, and the eval label vocabulary.

## Migrating stored outputs

Old exports fail validation against the new model (unknown field
`duplicate_relationship` value mapping). Either re-run classification, or
apply this mechanical mapping to stored `ClassificationResult` JSON:

```
duplicate_relationship            → relationship_assessment
  "Duplicates"                    →   "Same_case"
  "Otherwise_related"             →   "Different_case"
  "Abstain"                       →   "Abstain"
```

(`independence_reason` in old exports remains readable; the
`multi_patient_context_no_link` discrepancy in item 5 only affects newly
generated results.)

## Code-facing renames (for spec-level alignment on the secure side)

- Enum `DuplicateRelationship` → `RelationshipAssessment`
  (`SAME_CASE` / `DIFFERENT_CASE` / `ABSTAIN`).
- `ClassificationResult.duplicate_relationship` →
  `ClassificationResult.relationship_assessment`.
- `classifier.decision_logic` now exposes `assess_independence` and
  `assess_relationship`; `classify` composes them. The former
  shared-source-override dispatch is gone; behavior on `final_label` is
  preserved (verified by a 12,288-cell characterization golden test,
  `tests/test_decision_logic_characterization.py`).
- The runner's degraded-mode warning changed from "Shared-source override is
  disabled: …" to "Source-context evidence is disabled: …" (same trigger
  conditions). The run-manifest flag key
  `feature_flags.source_case_override_enabled` is **unchanged**.
