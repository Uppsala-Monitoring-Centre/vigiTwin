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
    """A reproducible record of one seeded sampling draw.

    Everything needed to reproduce or audit a draw: the procedure, the exact
    seed string handed to :class:`random.Random`, the package/python versions,
    and the resulting selection in draw order. The protocol requires that "the
    randomization procedure and seed will be recorded"; this is that record.
    """

    procedure_id: str
    seed: str
    created_at: datetime
    package_version: str | None = None
    python_version: str | None = None

    target: int | None = None
    n_eligible_clusters: int | None = None
    #: For tiered sampling (manual vs E2B-derived), the count available per tier.
    tier_counts: dict[str, int] = Field(default_factory=dict)
    #: For stratified SCLE sampling, the available population size per stratum.
    stratum_sizes: dict[str, int] = Field(default_factory=dict)
    #: The selected pair ids, in the order they were drawn.
    selected_pair_ids: list[str] = Field(default_factory=list)


__all__ = ["SamplingRecord"]
