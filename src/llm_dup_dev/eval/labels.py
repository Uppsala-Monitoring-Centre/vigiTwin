"""
Canonical label constants for the duplicate-detection pipeline and eval set.

Both the pipeline (decision_logic.classify) and the human-annotated eval set
use the same three-class vocabulary. This module defines the shared constants
and provides helpers for normalisation and Abstain detection.
"""

# --- Final classification labels ---
# Used by both classify() output and eval set "duplicates_assessment" column.
FINAL_LABELS = ("Duplicates", "Otherwise_related", "Unrelated")

# Mapping from lower-case / flexible forms to canonical labels.
_LABEL_MAP: dict[str, str] = {
    "duplicates": "Duplicates",
    "otherwise_related": "Otherwise_related",
    "unrelated": "Unrelated",
}


def normalise_label(label: str) -> str:
    """Map a label (case-insensitive) to the canonical FINAL_LABELS form.

    Accepts both pipeline output (e.g. "Duplicates") and annotation
    vocabulary (e.g. "duplicates", "otherwise_related").

    Raises ValueError for unknown labels (except Abstain — use is_abstain first).
    """
    key = label.strip().lower()
    if key in _LABEL_MAP:
        return _LABEL_MAP[key]
    raise ValueError(f"Unknown label: {label!r}")


def is_abstain(label: str) -> bool:
    """Check whether an eval-set assessment is Abstain (excluded from scoring)."""
    return label.strip().lower() == "abstain"
