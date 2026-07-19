"""Anchor population selection.

The anchor population defines the baseline population within which
collision probabilities are evaluated. A smaller anchor means the
shared clinical context is rarer, so additional matches carry more
evidential weight. Conversely, an unnarrowed anchor means the reports
share no clinical context, so the matching evidence is correspondingly
weak — see ``select_anchor`` on why that is N_total and never zero.

See: docs/duplicate_adjudication_spec.md § Anchor selection
"""

from llm_dup_dev.models.enums import AnchorType
from llm_dup_dev.models.pair_features import AnchorPopulation


def select_anchor(
    N_drugs: int,
    N_AEs: int,
    N_drugs_AEs: int,
    N_total: int,
    threshold: int = 50,
) -> AnchorPopulation:
    """Select the anchor population for probability estimation.

    Algorithm:
        0. If a matching set is empty, it does not narrow the population.
        1. If either N_drugs or N_AEs ≤ T, select the smaller of the two.
        2. Otherwise, use the drugs ∩ AEs intersection.

    A count of zero always means "the matching set is empty", never "the
    population is empty": the counts include the two focal reports, so a
    non-empty matching set forces its count to at least 2. Every report
    vacuously contains the empty set, so an empty matching set leaves the
    population unnarrowed rather than empty — when *both* sets are empty
    the anchor is therefore the whole reporting context, N_total.

    Parameters
    ----------
    N_drugs : int
        Reports in context containing all matching drugs. Zero iff no
        drugs match.
    N_AEs : int
        Reports in context containing all matching adverse events. Zero
        iff no AEs match.
    N_drugs_AEs : int
        Reports in context containing both matching drugs and AEs.
    N_total : int
        Total reports in the reporting context. Used as the anchor when
        neither matching set narrows the population.
    threshold : int
        Configurable threshold T (default 50).

    Returns
    -------
    AnchorPopulation
        Selected anchor with type and size.
    """
    # Neither set narrows the population: the anchor is the whole context.
    # Reporting zero here would be wrong in a way that inverts the method —
    # it makes the multiplicity M = C(N_anchor, 2) vanish, so P_match = 0
    # vacuously and L_match saturates at its ceiling, turning "these reports
    # have nothing in common" into maximal duplicate evidence.
    if N_AEs == 0 and N_drugs == 0:
        anchor_type = AnchorType.DRUGS
        N_anchor = N_total
    # When exactly one set is empty, it imposes no restriction, so the
    # other set is the anchor.
    elif N_AEs == 0:
        anchor_type = AnchorType.DRUGS
        N_anchor = N_drugs
    elif N_drugs == 0:
        anchor_type = AnchorType.AES
        N_anchor = N_AEs
    elif N_drugs <= threshold or N_AEs <= threshold:
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
