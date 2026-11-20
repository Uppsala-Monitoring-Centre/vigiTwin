"""
Canonical label constants for the duplicate-detection pipeline and eval set.

Both the pipeline (decision_logic.classify) and the human-annotated eval set
use the same three-class decided vocabulary (Duplicates / Otherwise_related /
Unrelated). Pipeline output may additionally carry the value ``Abstain`` for
epistemically underdetermined same-source scenarios.

``AbstainMode`` controls how pipeline abstains are handled during evaluation.
"""

from enum import Enum


class AbstainMode(str, Enum):
    """Controls how pipeline-produced ``Abstain`` results are treated during eval.

    Pipeline abstains are produced exclusively for epistemically
    underdetermined same-source scenarios — they are not generic
    uncertainty. The three modes answer different evaluation questions:

    ``DECIDED_ONLY``
        Exclude pairs where the pipeline abstained. Answers: "How
        accurate is the system on pairs where it reached a decision?"

    ``ABSTAIN_AS_DUPLICATE``
        Treat all pipeline abstains as ``Duplicates``. Provides a
        lower bound on non-duplicate recall (upper bound on duplicate
        recall). Answers: "What is performance if we assume every
        abstained pair is a duplicate?"

    ``ABSTAIN_AS_NONDUPLICATE``
        Treat all pipeline abstains as ``Otherwise_related``. Provides
        an upper bound on non-duplicate recall (lower bound on duplicate
        recall). Answers: "What is performance if we assume every
        abstained pair is not a duplicate?"
    """

    DECIDED_ONLY = "decided_only"
    ABSTAIN_AS_DUPLICATE = "abstain_as_duplicate"
    ABSTAIN_AS_NONDUPLICATE = "abstain_as_nonduplicate"


# --- Final classification labels (decided outcomes only) ---
# Used for the 3x3 confusion matrix; Abstain pairs are excluded or
# remapped before entering this vocabulary.
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
    """Check whether a label is Abstain (pipeline or annotation)."""
    return label.strip().lower() == "abstain"
