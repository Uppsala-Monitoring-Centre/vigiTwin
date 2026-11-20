"""Union-grouping of a report pair's drugs and adverse events.

Both review formats present drugs and adverse events as a *union* of the two
reports, one row per normalised entity, with every instance of that entity on
each side kept. The grouping is identical for both formats — only what is
done with the groups differs — so it lives here rather than in either
builder.

Extracted verbatim from ``compare._build_drug_rows`` / ``_build_ae_rows``;
the key order it produces is unchanged (see :func:`grouped_union`).
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Callable, Iterable, Sequence, TypeVar

from vigitwin.models.report import AdverseEvent, Drug

from .normalize import normalize_ae_key, normalize_drug_key

T = TypeVar("T")


def group_by_key(
    items: Iterable[T], key_fn: Callable[[T], str]
) -> "OrderedDict[str, list[T]]":
    """Group *items* by ``key_fn``, preserving first-seen key order."""
    groups: OrderedDict[str, list[T]] = OrderedDict()
    for item in items:
        groups.setdefault(key_fn(item), []).append(item)
    return groups


def grouped_union(
    left_items: Sequence[T],
    right_items: Sequence[T],
    key_fn: Callable[[T], str],
) -> list[tuple[str, list[T], list[T]]]:
    """Return ``(key, left_group, right_group)`` for the union of both sides.

    Keys are returned in ``sorted()`` order — alphabetical by normalised key,
    not report order. That is deliberate and pre-existing: a reader comparing
    several pairs needs the same entity in the same place, and the order must
    not depend on which report happened to be loaded first. Either group may
    be empty, never both.
    """
    left_groups = group_by_key(left_items, key_fn)
    right_groups = group_by_key(right_items, key_fn)

    all_keys: list[str] = list(left_groups.keys())
    for key in right_groups:
        if key not in left_groups:
            all_keys.append(key)

    return [
        (key, left_groups.get(key, []), right_groups.get(key, []))
        for key in sorted(all_keys)
    ]


def grouped_drugs(
    left: Sequence[Drug], right: Sequence[Drug]
) -> list[tuple[str, list[Drug], list[Drug]]]:
    """Union-group two reports' drugs by normalised substance name."""
    return grouped_union(left, right, lambda d: normalize_drug_key(d.substance))


def grouped_adverse_events(
    left: Sequence[AdverseEvent], right: Sequence[AdverseEvent]
) -> list[tuple[str, list[AdverseEvent], list[AdverseEvent]]]:
    """Union-group two reports' adverse events by normalised preferred term."""
    return grouped_union(left, right, lambda ae: normalize_ae_key(ae.pt_name))


__all__ = [
    "group_by_key",
    "grouped_adverse_events",
    "grouped_drugs",
    "grouped_union",
]
