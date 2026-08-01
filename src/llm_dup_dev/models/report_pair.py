"""Data models for the ReportPair layer.

The ReportPair layer defines candidate pairs of reports to evaluate
for possible duplication. It contains only identifiers; all derived
information lives in the PairFeatures layer.

See: docs/data_contracts.md § ReportPair Layer
"""

from __future__ import annotations

from pydantic import BaseModel, ValidationInfo, model_validator

from .pair_identity import (
    canonical_pair_id,
    canonical_report_ids,
    validate_pair_identity,
)
from .report import Report

#: Validation-context key that relaxes the canonical-identity check when
#: reading an artifact written before canonicalisation. See
#: :meth:`ReportPair._check_identity`.
ALLOW_NONCANONICAL_CONTEXT_KEY = "allow_noncanonical_pair_identity"


class ReportPair(BaseModel):
    """Identifies a candidate pair of reports to evaluate for duplication.

    Contains only identifiers. All derived information used in
    duplicate assessment is stored in the PairFeatures layer.

    A pair has one identity regardless of the order its two report ids are
    supplied in. The report ids are stored in canonical ascending order —
    numerically for numeric ids, so ``9`` precedes ``10`` — and the pair_id is
    always derived from that order:

        {report_1_id}__{report_2_id}    with report_1_id < report_2_id

    A report is not a candidate duplicate of itself, so the two ids must
    differ, and neither may be blank or contain the ``__`` separator.

    Build a pair with :meth:`from_report_ids` or :meth:`from_reports`, which
    canonicalise for you. Validation of an existing record is deliberately
    strict rather than corrective: pair_id is the sole join key against stored
    features, annotations and predictions, so a noncanonical record is rejected
    and the artifact regenerated rather than silently rewritten. Passing
    ``context={"allow_noncanonical_pair_identity": True}`` to ``model_validate``
    relaxes the ordering and derivation checks for reading pre-canonicalisation
    artifacts; blank ids and self-pairs stay rejected in every mode.

    Note that pydantic does not coerce ``int`` to ``str``, so numeric report ids
    coming out of a database must be converted by the caller.
    """

    pair_id: str
    report_1_id: str
    report_2_id: str

    @classmethod
    def from_report_ids(cls, report_id_a: str, report_id_b: str) -> "ReportPair":
        """Build a canonical pair from two report ids given in any order."""
        lower, higher = canonical_report_ids(report_id_a, report_id_b)
        return cls(
            pair_id=canonical_pair_id(lower, higher),
            report_1_id=lower,
            report_2_id=higher,
        )

    @classmethod
    def from_reports(cls, report_a: Report, report_b: Report) -> "ReportPair":
        """Build a canonical pair from two reports given in any order."""
        return cls.from_report_ids(report_a.report_id, report_b.report_id)

    @model_validator(mode="after")
    def _check_identity(self, info: ValidationInfo) -> "ReportPair":
        context = info.context or {}
        if context.get(ALLOW_NONCANONICAL_CONTEXT_KEY):
            # Relaxed mode, for rendering artifacts written before pair ids were
            # canonical. Still reject what is nonsense in any artifact: unusable
            # report ids and a report paired with itself.
            canonical_report_ids(self.report_1_id, self.report_2_id)
            return self
        validate_pair_identity(self.pair_id, self.report_1_id, self.report_2_id)
        return self
