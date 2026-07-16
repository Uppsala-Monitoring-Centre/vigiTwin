"""Controlled vocabulary and normalization helpers for demographic fields.

These are shared by the structured :class:`~llm_dup_dev.models.report.Report`
model validators and the narrative-extraction models so that "value means
absence of information" is defined in exactly one place.

Rationale
---------
The data contract (see ``docs/data_contracts.md`` § Patient Sex) states that
values representing uncertainty (e.g. ``"unknown"``) must **not** be stored in
the multi-valued demographic lists — missing information is an empty list, not a
sentinel string. The narrative-extraction path already enforced this; these
helpers extend the same rule to the structured fields populated by the
secure-side report loader, and to patient sex specifically restrict the stored
value to the E2B-aligned binary vocabulary (``male``/``female``).

All helpers **drop** offending entries rather than raising, so legacy frozen
bundles that still contain e.g. ``"unknown"`` remain loadable.
"""

from __future__ import annotations

from typing import Any

# Categorical "values" that actually signal absence of information. Dropped
# rather than recorded, so a structured field of "not known" yields an empty
# list — consistent with the empty-list-means-missing contract.
UNCERTAINTY_SENTINELS: frozenset[str] = frozenset(
    {
        "",
        "unknown",
        "not known",
        "unspecified",
        "not reported",
        "not stated",
        "not specified",
        "not available",
        "n/a",
        "na",
        "none",
        "?",
    }
)

# E2B aligns structured patient sex to a binary vocabulary (1=male, 2=female);
# unknown is a null flavour, not a value. Anything outside this set — including
# "other" and any uncertainty sentinel — is treated as missing.
STRUCTURED_SEX_VOCAB: frozenset[str] = frozenset({"male", "female"})


def _extract_value(item: Any) -> tuple[Any, str | None, list[Any] | None]:
    """Return ``(raw_value, canonical_key, sources)`` for one list entry.

    Handles the three shapes a ``mode="before"`` validator may see: a
    ``SourcedValue`` instance, a ``dict`` (JSON / frozen-bundle deserialization),
    or a bare value. ``canonical_key`` is the lower-cased, stripped string used
    for vocabulary lookups, or ``None`` when the value is not a string.
    ``sources`` is returned when available so callers can preserve provenance.
    """
    value = getattr(item, "value", None)
    sources = getattr(item, "sources", None)
    if value is None and isinstance(item, dict):
        value = item.get("value")
        sources = item.get("sources")
    if value is None and not isinstance(item, dict):
        value = item

    key = value.strip().lower() if isinstance(value, str) else None
    return value, key, sources


def normalize_structured_sex(values: object) -> object:
    """Keep only ``male``/``female`` sex candidates, canonicalised to lowercase.

    Non-list input is returned unchanged so pydantic still emits its normal
    type error. Each kept entry preserves its ``sources`` and has its value
    rewritten to the canonical lowercase form (``"Male"`` -> ``"male"``).
    Everything else — ``"other"``, uncertainty sentinels, blanks — is dropped.
    """
    if not isinstance(values, list):
        return values

    kept: list[Any] = []
    for item in values:
        _, key, sources = _extract_value(item)
        if key not in STRUCTURED_SEX_VOCAB:
            continue
        if sources is not None:
            kept.append({"value": key, "sources": sources})
        else:
            kept.append(key)
    return kept


def drop_uncertainty_values(values: object) -> object:
    """Drop entries whose value is an uncertainty sentinel; keep the rest as-is.

    Used for free-text categorical fields (e.g. country) where the domain of
    valid values cannot be enumerated. Non-list input is returned unchanged.
    """
    if not isinstance(values, list):
        return values

    kept: list[Any] = []
    for item in values:
        _, key, _ = _extract_value(item)
        if key is not None and key in UNCERTAINTY_SENTINELS:
            continue
        kept.append(item)
    return kept
