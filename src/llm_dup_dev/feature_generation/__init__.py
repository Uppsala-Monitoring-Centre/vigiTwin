"""Feature generation for the same-individual assessment pipeline.

Computes PairFeatures from two Report objects and pre-computed
frequency statistics.

Modules:
    matching            — Drug/AE set intersection, demographic comparison
    reporting_context   — Determine shared reporting context
    anchor              — Anchor population selection
    probability         — Collision probability and L_match computation
    inconsistencies     — Detect demographic and narrative inconsistencies
    pipeline            — Orchestrate all steps into PairFeatures
"""
