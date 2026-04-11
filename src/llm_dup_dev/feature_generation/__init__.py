"""Feature generation for the duplicate adjudication pipeline.

Computes PairFeatures from two Report objects and pre-computed
frequency statistics. The resulting features are consumed by the
report-pair classification decision logic in ``llm_dup_dev.classifier``.

Modules:
    matching            — Drug/AE set intersection, demographic comparison
    reporting_context   — Determine shared reporting context
    anchor              — Anchor population selection
    probability         — Collision probability and L_match computation
    inconsistencies     — Detect demographic and narrative inconsistencies
    pipeline            — Orchestrate all steps into PairFeatures
"""
