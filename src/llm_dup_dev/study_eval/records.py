"""Small provenance records shared across the study-eval package.

Kept in its own module (with no study-eval-internal imports) so that both
:mod:`llm_dup_dev.study_eval.schemas` and
:mod:`llm_dup_dev.study_eval.sampling` can depend on it without forming an
import cycle.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SamplingRecord(BaseModel):
    """A reproducible record of one seeded SCLE sampling draw.

    Everything needed to reproduce or audit a draw: the procedure, the exact
    seed string handed to :class:`random.Random`, the package/python versions,
    the available population per stratum, and the resulting selection in draw
    order. The protocol requires that "the randomization procedure and seed will
    be recorded"; this is that record.

    The dev side only samples SCLE cases (the recall/precision reference sets are
    constructed on the secure side), so this record describes a stratified SCLE
    draw — not any cluster-construction sampling.
    """

    procedure_id: str
    seed: str
    created_at: datetime
    package_version: str | None = None
    python_version: str | None = None

    #: The per-stratum cap requested for this draw (``per_cell`` / ``per_stratum``).
    per_stratum_target: int | None = None
    #: Available population size per stratum (stratum key -> count).
    stratum_sizes: dict[str, int] = Field(default_factory=dict)
    #: The selected pair ids, in the order they were drawn (primary picks only).
    selected_pair_ids: list[str] = Field(default_factory=list)
    #: The ordered backup pair ids per stratum (stratum key -> ranked list).
    backup_pair_ids: dict[str, list[str]] = Field(default_factory=dict)


__all__ = ["SamplingRecord"]
