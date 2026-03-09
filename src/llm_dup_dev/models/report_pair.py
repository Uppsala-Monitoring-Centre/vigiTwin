"""Data models for the ReportPair layer.

The ReportPair layer defines candidate pairs of reports to evaluate
for possible duplication. It contains only identifiers; all derived
information lives in the PairFeatures layer.

See: docs/data_contracts.md § ReportPair Layer
"""

from pydantic import BaseModel


class ReportPair(BaseModel):
    """Identifies a candidate pair of reports to evaluate for duplication.

    Contains only identifiers. All derived information used in
    duplicate assessment is stored in the PairFeatures layer.

    The pair_id is typically constructed deterministically as:
        {report_1_id}__{report_2_id}
    """

    pair_id: str
    report_1_id: str
    report_2_id: str
