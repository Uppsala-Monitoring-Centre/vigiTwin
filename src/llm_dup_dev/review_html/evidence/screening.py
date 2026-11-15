"""Reading a screening algorithm's own verdict out of its per-pair entry.

A :class:`~llm_dup_dev.data.offline.ScreeningDetails` entry is deliberately
untyped at the feature level — screening algorithms differ, and the artifact
renders whatever its producer put there. That leaves one thing the evidence
review needs and cannot assume: whether the algorithm *flagged* this pair.

Presence of an entry is not the answer. An export may carry a row per pair
considered, verdict included, in which case a present entry can mean "not
flagged"; another may list only the pairs it flagged, in which case absence
means nothing at all. Treating presence as a verdict would read the second
kind correctly and the first kind backwards.

So the verdict is read from a named boolean in the entry itself. The keys
below are the ones known to carry it; a caller whose export uses a different
name passes that name instead. A key that is absent leaves the verdict unknown
and the tile reads "Not evaluated" — never "Not flagged", which is a claim no
one made. A key that is present but does not hold a boolean is reported as a
gap rather than coerced: a verdict is exactly the kind of value that must not
be guessed from a truthy string.
"""

from __future__ import annotations

from typing import NamedTuple

__all__ = ["SCREENING_VERDICT_KEYS", "ScreeningVerdict", "screening_verdict"]


#: Keys a screening entry may carry its own verdict under, in the order tried.
#:
#: ``IsSuspectedDuplicate`` is what the vigiMatch prediction export writes.
#: ``screening_retrieved`` is this repo's own name for the same fact — the
#: field on :class:`~llm_dup_dev.study_eval.schemas.PairPrediction` — so an
#: export generated from a prediction set is read without configuration.
SCREENING_VERDICT_KEYS: tuple[str, ...] = (
    "IsSuspectedDuplicate",
    "screening_retrieved",
    "retrieved",
    "flagged",
)


class ScreeningVerdict(NamedTuple):
    """What the entry said, and what could not be read.

    ``retrieved`` is ``None`` when no verdict was found — the pair has no
    entry, or the entry carries none of the known keys. ``note`` is empty
    unless something was found and rejected, in which case it is the sentence
    the page shows as a gap.
    """

    retrieved: bool | None
    note: str = ""


def screening_verdict(
    entry: dict | None, *, key: str | None = None
) -> ScreeningVerdict:
    """Read the screening verdict out of one pair's entry.

    Parameters
    ----------
    entry : dict, optional
        The pair's ``ScreeningDetails`` entry, or ``None`` when the artifact
        does not cover this pair.
    key : str, optional
        The name the verdict is stored under. When given it is the only key
        consulted, and its absence is reported — a caller who names a key
        expects it to be there. When omitted, :data:`SCREENING_VERDICT_KEYS`
        are tried in order and none being present is simply "no verdict".

    Notes
    -----
    Only a JSON boolean counts. ``1``, ``"true"`` and ``"yes"`` are rejected
    with a note rather than coerced: the cost of reading a verdict backwards
    is a page that tells a reader another method agreed when it did not.
    """
    if not entry:
        return ScreeningVerdict(None)

    if key is not None:
        if key not in entry:
            return ScreeningVerdict(
                None,
                f"The screening verdict key {key!r} is not present in this "
                "pair's screening entry, so no screening outcome is shown.",
            )
        return _as_bool(entry[key], key)

    for candidate in SCREENING_VERDICT_KEYS:
        if candidate in entry:
            return _as_bool(entry[candidate], candidate)
    return ScreeningVerdict(None)


def _as_bool(value: object, key: str) -> ScreeningVerdict:
    """``value`` as a verdict, or a note saying why it is not one."""
    if isinstance(value, bool):
        return ScreeningVerdict(value)
    return ScreeningVerdict(
        None,
        f"The screening entry's {key!r} is {value!r}, which is not a true or "
        "false verdict, so no screening outcome is shown.",
    )
