"""Canonical identity for a report pair.

A pair of reports has **one** identity, independent of the order in which the
two report ids are supplied. The two ids are stored in canonical ascending
order and ``pair_id`` is always derived from that order::

    {lower_report_id}__{higher_report_id}

So ``(A, B)`` and ``(B, A)`` are the same pair with the same ``pair_id``.

Why this matters
----------------
``PairFeatures`` and ``ClassificationResult`` carry only a ``pair_id`` — no
report ids. ``pair_id`` is therefore the *sole* join key between stored
features, classifications, gold annotations, reference sets and frozen
predictions. Before canonicalisation, ``A__B`` and ``B__A`` were different ids
for the same two reports, so an annotation file that happened to list a pair
the other way round matched nothing and the evaluation scored zero pairs while
still reporting a number.

Construct, or validate — never silently rewrite
-----------------------------------------------
:func:`canonical_pair_id` is for *constructing* a new pair.
:func:`validate_pair_identity` is for *reading* one back. Data that is already
frozen must fail loudly rather than be normalised in place: rewriting a
``pair_id`` inside an artifact is precisely what would break its join against
annotations or predictions still keyed on the old id. The supported repair is
to regenerate the artifact.

Ordering convention
-------------------
Numeric ids are ordered **numerically**, so ``9`` sorts before ``10`` rather
than after it as plain string comparison would have it. Everything else is
ordered lexicographically, and numeric ids sort before non-numeric ones.

The convention is arbitrary-but-total, not semantic: for a mixed pair it merely
picks a stable answer (``("9", "R10")`` canonicalises to ``9__R10``). Nothing
should read meaning into which id came first.

**The convention is frozen once artifacts exist.** Changing
:func:`report_id_sort_key` would silently re-key every stored pair and break
every join against previously written features, annotations and predictions.
"""

from __future__ import annotations

import re

__all__ = [
    "PAIR_ID_SEPARATOR",
    "canonical_pair_id",
    "canonical_report_ids",
    "report_id_sort_key",
    "split_pair_id",
    "validate_pair_identity",
    "validate_report_id",
]

#: Separator between the two report ids in a ``pair_id``.
PAIR_ID_SEPARATOR = "__"

#: A report id that can be ordered numerically. Deliberately ASCII-only rather
#: than ``str.isdigit()``, which is true for unicode digits that ``int()``
#: accepts but no report id should contain.
_NUMERIC_REPORT_ID = re.compile(r"[0-9]+")


def report_id_sort_key(report_id: str) -> tuple[int, int, str]:
    """Return the sort key placing ``report_id`` in canonical order.

    Numeric ids sort numerically and ahead of non-numeric ones; everything else
    sorts lexicographically. The raw string is the final tiebreak so that
    leading-zero variants (``"007"`` and ``"7"``) remain a **total** order
    rather than comparing equal.

    See the module docstring: this ordering is frozen once artifacts exist.
    """
    if _NUMERIC_REPORT_ID.fullmatch(report_id):
        return (0, int(report_id), report_id)
    return (1, 0, report_id)


def validate_report_id(report_id: str, *, field: str = "report id") -> None:
    """Raise ``ValueError`` unless ``report_id`` can take part in a ``pair_id``.

    Rejects blank ids and any id containing :data:`PAIR_ID_SEPARATOR`, which
    would make the resulting ``pair_id`` impossible to split back into its two
    halves unambiguously.

    Does **not** strip whitespace. Silent normalisation is the behaviour this
    module exists to remove; callers that legitimately need to strip (such as
    the annotation-file loader) do so on their own input first.
    """
    if not isinstance(report_id, str) or not report_id.strip():
        raise ValueError(
            f"{field} must be a non-blank string, got {report_id!r}. A pair is "
            f"identified by its two report ids, so a blank id has no identity."
        )
    if PAIR_ID_SEPARATOR in report_id:
        raise ValueError(
            f"{field} {report_id!r} contains {PAIR_ID_SEPARATOR!r}, which is "
            f"the pair_id separator. A pair_id built from it could not be split "
            f"back into two report ids unambiguously."
        )


def canonical_report_ids(report_id_a: str, report_id_b: str) -> tuple[str, str]:
    """Return the two report ids in canonical ascending order.

    Parameters
    ----------
    report_id_a, report_id_b : str
        The two report ids, in any order.

    Returns
    -------
    tuple of str
        ``(lower, higher)`` under :func:`report_id_sort_key`.

    Raises
    ------
    ValueError
        If either id is blank or contains the pair_id separator, or if the two
        ids are equal (a report is not a candidate duplicate of itself).
    """
    validate_report_id(report_id_a, field="report_id_a")
    validate_report_id(report_id_b, field="report_id_b")
    if report_id_a == report_id_b:
        raise ValueError(
            f"a pair needs two distinct reports, got {report_id_a!r} twice. A "
            f"report is not a candidate duplicate of itself."
        )
    if report_id_sort_key(report_id_a) <= report_id_sort_key(report_id_b):
        return (report_id_a, report_id_b)
    return (report_id_b, report_id_a)


def canonical_pair_id(report_id_a: str, report_id_b: str) -> str:
    """Return the canonical ``pair_id`` for two report ids, in any order.

    ``canonical_pair_id(a, b) == canonical_pair_id(b, a)`` always. This is the
    single construction site for a ``pair_id``; see the module docstring for why
    existing artifacts must be validated rather than re-derived.
    """
    lower, higher = canonical_report_ids(report_id_a, report_id_b)
    return f"{lower}{PAIR_ID_SEPARATOR}{higher}"


def split_pair_id(pair_id: str) -> tuple[str, str]:
    """Return the two report ids in a ``pair_id``.

    The inverse of :func:`canonical_pair_id`, and unambiguous because
    :func:`validate_report_id` bans the separator inside a report id.

    Raises
    ------
    ValueError
        If ``pair_id`` does not split into exactly two non-blank halves.
    """
    parts = pair_id.split(PAIR_ID_SEPARATOR)
    if len(parts) != 2 or not all(part.strip() for part in parts):
        raise ValueError(
            f"pair_id {pair_id!r} does not split into exactly two non-blank "
            f"report ids on {PAIR_ID_SEPARATOR!r}."
        )
    return (parts[0], parts[1])


def validate_pair_identity(
    pair_id: str,
    report_1_id: str,
    report_2_id: str,
    *,
    context: str = "",
) -> None:
    """Raise ``ValueError`` unless the three fields form a canonical identity.

    The read-path counterpart to :func:`canonical_pair_id`: use this when
    loading a pair from an artifact someone else wrote.

    Parameters
    ----------
    pair_id, report_1_id, report_2_id : str
        The triple to check.
    context : str, optional
        Prefix for the error message, e.g. a file path or a set id.

    Raises
    ------
    ValueError
        If a report id is unusable, the two ids are the same report, the ids are
        not in canonical order, or ``pair_id`` is not the id derived from them.
        Each case reports separately, and each says to regenerate rather than
        repair in place.
    """
    where = f"{context}: " if context else ""
    fix = (
        "Regenerate the artifact from the canonical helpers rather than "
        "reordering it in place — pair_id is the join key against annotations "
        "and predictions, so rewriting it here would silently break those joins."
    )

    validate_report_id(report_1_id, field=f"{where}report_1_id")
    validate_report_id(report_2_id, field=f"{where}report_2_id")

    if report_1_id == report_2_id:
        raise ValueError(
            f"{where}a pair needs two distinct reports, got {report_1_id!r} "
            f"twice (pair_id={pair_id!r}). A report is not a candidate "
            f"duplicate of itself."
        )

    lower, higher = canonical_report_ids(report_1_id, report_2_id)
    if (report_1_id, report_2_id) != (lower, higher):
        raise ValueError(
            f"{where}report ids are not in canonical order: report_1_id="
            f"{report_1_id!r}, report_2_id={report_2_id!r} (expected "
            f"report_1_id={lower!r}, report_2_id={higher!r}). Report ids are "
            f"ordered ascending, numerically for numeric ids. {fix}"
        )

    expected = f"{lower}{PAIR_ID_SEPARATOR}{higher}"
    if pair_id != expected:
        raise ValueError(
            f"{where}pair_id {pair_id!r} is inconsistent with its report ids "
            f"(expected {expected!r} from report_1_id={report_1_id!r}, "
            f"report_2_id={report_2_id!r}). {fix}"
        )
