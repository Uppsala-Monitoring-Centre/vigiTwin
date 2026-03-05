"""
Canonical label constants for the duplicate-detection pipeline and eval set.

Both the pipeline (decision_logic.classify_pair) and the human-annotated eval set
use the same vocabulary, so no mapping layer is needed. This module defines the
shared constants and provides a helper to identify Abstain annotations.
"""

# --- Final classification labels ---
# Used by both classify_pair() output and eval set "assessment" column.
FINAL_LABELS = ("Duplicates", "Otherwise_related", "Unrelated")

# --- Per-field label vocabularies ---
SAME_PATIENT_VALUES = ("same", "different", "unclear")
AE_CONSISTENCY_VALUES = ("consistent", "partially_consistent", "inconsistent")
CLUSTER_VALUES = ("possible_cluster", "no_evidence_of_cluster")


def is_abstain(label: str) -> bool:
    """Check whether an eval-set assessment is Abstain (excluded from scoring)."""
    return label.strip().lower() == "abstain"
