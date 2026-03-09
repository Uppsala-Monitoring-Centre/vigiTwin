"""Anchor population selection.

The anchor population defines the baseline population within which
collision probabilities are evaluated. A smaller anchor means the
shared clinical context is rarer, so additional matches carry more
evidential weight.

See: docs/same_individual_assessment_spec.md § Anchor Selection
"""

from llm_dup_dev.models.enums import AnchorType
from llm_dup_dev.models.pair_features import AnchorPopulation


def select_anchor(
    N_drugs: int,
    N_AEs: int,
    N_drugs_AEs: int,
    threshold: int = 50,
) -> AnchorPopulation:
    """Select the anchor population for probability estimation.

    Algorithm:
        1. If either N_drugs or N_AEs ≤ T, select the smaller of the two.
        2. Otherwise, use the drugs ∩ AEs intersection.

    Parameters
    ----------
    N_drugs : int
        Reports in context containing all matching drugs.
    N_AEs : int
        Reports in context containing all matching adverse events.
    N_drugs_AEs : int
        Reports in context containing both matching drugs and AEs.
    threshold : int
        Configurable threshold T (default 50).

    Returns
    -------
    AnchorPopulation
        Selected anchor with type and size.
    """
    if N_drugs <= threshold or N_AEs <= threshold:
        # At least one candidate is small — use the smaller
        if N_drugs <= N_AEs:
            anchor_type = AnchorType.DRUGS
            N_anchor = N_drugs
        else:
            anchor_type = AnchorType.AES
            N_anchor = N_AEs
    else:
        # Both are large — use the intersection
        anchor_type = AnchorType.DRUGS_AES
        N_anchor = N_drugs_AEs

    return AnchorPopulation(
        N_drugs=N_drugs,
        N_AEs=N_AEs,
        N_drugs_AEs=N_drugs_AEs,
        type=anchor_type,
        N_anchor=N_anchor,
    )
