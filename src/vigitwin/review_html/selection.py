"""Selecting a subset of a saved run's pairs before anything is built.

A saved run is organised by reference set, and a curated collection usually
wants two or three pairs out of thirty. Selection happens on the pairs
artifact, before any per-pair view model is built, so the cost of a two-pair
collection is reading the set-level JSON once — not building twenty-nine view
models and discarding them.

Selection deliberately has its own error policy, separate from ``strict``:

* ``strict`` governs pairs the caller did *not* ask for by name. Warning and
  skipping one is reasonable — the caller asked for "everything renderable".
* A pair the caller *named* is different. Silently dropping it leaves a
  curated collection quietly short of an example someone deliberately chose,
  and nothing in the output says so. So a named pair that cannot be loaded is
  an error by default, and ``MissingPairPolicy.SKIP`` is the opt-in.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, Sequence, TypeVar

T = TypeVar("T")


class MissingPairPolicy(str, Enum):
    """What to do when an explicitly selected pair cannot be loaded."""

    ERROR = "error"
    SKIP = "skip"


@dataclass(frozen=True)
class PairSelection:
    """An explicit set of pair ids to render, in the order they were given."""

    pair_ids: tuple[str, ...]
    on_missing: MissingPairPolicy = MissingPairPolicy.ERROR
    #: Emit pairs in selection order rather than artifact order. The default,
    #: because a curated collection's order is a deliberate choice.
    preserve_order: bool = True

    def __post_init__(self) -> None:
        if not self.pair_ids:
            raise ValueError(
                "PairSelection needs at least one pair id; pass select=None "
                "to render every pair."
            )
        counts = Counter(self.pair_ids)
        duplicates = sorted(pid for pid, n in counts.items() if n > 1)
        if duplicates:
            raise ValueError(
                f"PairSelection contains duplicate pair id(s): "
                f"{', '.join(duplicates)}."
            )


def coerce_selection(
    select: PairSelection | Iterable[str] | None,
) -> PairSelection | None:
    """Accept a bare iterable of ids as a selection with default policy."""
    if select is None or isinstance(select, PairSelection):
        return select
    return PairSelection(pair_ids=tuple(select))


def apply_selection(
    pairs: Sequence[T],
    selection: PairSelection | None,
    *,
    key: Callable[[T], str],
    artifact: str = "pairs",
) -> list[T]:
    """Narrow *pairs* to the selection, preserving the requested order.

    Every unknown id is reported at once rather than one per run: a curated
    manifest with three stale ids should say so in one error, not three
    edit-and-retry cycles. This is raised regardless of ``strict``, because
    an id that matches nothing is a mistake in the request itself, not a
    defect in the data.
    """
    if selection is None:
        return list(pairs)

    by_id = {key(pair): pair for pair in pairs}
    unknown = [pid for pid in selection.pair_ids if pid not in by_id]
    if unknown:
        available = len(by_id)
        raise ValueError(
            f"Selected pair id(s) not present in the {artifact} artifact: "
            f"{', '.join(unknown)}. The artifact holds {available} pair"
            f"{'' if available == 1 else 's'}. Pair ids are canonical "
            f"({{report_id1}}__{{report_id2}}, ids ascending), so check the "
            f"orientation as well as the ids themselves."
        )

    if selection.preserve_order:
        return [by_id[pid] for pid in selection.pair_ids]
    wanted = set(selection.pair_ids)
    return [pair for pair in pairs if key(pair) in wanted]


__all__ = [
    "MissingPairPolicy",
    "PairSelection",
    "apply_selection",
    "coerce_selection",
]
