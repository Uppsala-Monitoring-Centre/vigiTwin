"""The run settings an evidence review needs but a saved run does not record.

A saved run's ``pair_features.json`` carries the evidence and the scores, and
its ``classifications.json`` carries the decision — including the inconsistency
penalty's own threshold, so the same-case comparison is always reportable. Two
things are *not* in either file:

* ``epsilon``, which lives on :class:`~llm_dup_dev.classifier.config.PipelineConfig`.
  It sets the score ceiling: ``L_match = max(0, -log10(p_match + epsilon))``,
  so with the default ``1e-15`` a chance-match probability far below that still
  yields a score of about 15. Without epsilon there is no way to say whether a
  score sits at its ceiling.
* the ``L_match`` zone boundaries, which live on
  :class:`~llm_dup_dev.classifier.config.DecisionThresholds`. The *reason* a
  pair rejected independence is stored; the number it was compared against is
  not.

Both are recorded in a :class:`~llm_dup_dev.run_manifest.models.RunManifest`.
When one is available these settings make the evidence review quote the run's
own numbers. When it is not, the review says what it knows — the zone by name,
the reason by name — and quotes no threshold at all. It never falls back to the
current defaults: a run that used custom thresholds would then be described
with numbers that did not apply to it, which is worse than saying nothing.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from pathlib import Path

import yaml
from pydantic import BaseModel

from .models import ProvenanceVM

#: Keys read out of a run manifest, by manifest section.
_PIPELINE_KEYS = ("epsilon",)
_THRESHOLD_KEYS = (
    "lmatch_compelling",
    "lmatch_intermediate",
    "lmatch_limited",
    "lpatient_compelling",
    "explicit_link_enabled",
)


class EvidenceRunSettings(BaseModel):
    """The decision settings a run actually used, where they are known.

    Every field is optional: a partially recorded manifest yields partial
    settings rather than an error, and each consumer checks the one field it
    needs. ``source`` names where the values came from, so a page can say so.
    """

    epsilon: float | None = None
    lmatch_compelling: float | None = None
    lmatch_intermediate: float | None = None
    lmatch_limited: float | None = None
    lpatient_compelling: float | None = None
    explicit_link_enabled: bool | None = None

    #: Run provenance, for the page footer. Read from the manifest's own
    #: identity fields rather than reconstructed: ``dev_package_version`` is
    #: the version that produced the *run*, which is not necessarily the one
    #: rendering the review.
    run_id: str | None = None
    created_at: str | None = None
    dev_package_version: str | None = None
    dev_git_commit: str | None = None

    #: Human-readable provenance, e.g. a manifest path, or "" when supplied
    #: directly by a caller.
    source: str = ""

    @property
    def configuration_text(self) -> str:
        """The decision settings this run used, rendered for a footer.

        Only what the manifest actually recorded: a run that kept none of it
        yields an empty string, and the footer then omits the clause rather
        than implying the defaults applied.
        """
        parts: list[str] = []
        zones = [
            self.lmatch_compelling,
            self.lmatch_intermediate,
            self.lmatch_limited,
        ]
        if all(z is not None for z in zones):
            # One decimal, as every score on the page is shown, so a reader
            # is not comparing "3" against "3.0".
            joined = " / ".join(f"{z:.1f}" for z in zones if z is not None)
            parts.append(f"L_match thresholds {joined}")
        if self.lpatient_compelling is not None:
            parts.append(f"L_patient {self.lpatient_compelling:.1f}")
        if self.epsilon is not None:
            parts.append(f"epsilon {self.epsilon:g}")
        if self.explicit_link_enabled is not None:
            parts.append(
                "explicit links "
                + ("enabled" if self.explicit_link_enabled else "disabled")
            )
        return "; ".join(parts)

    @property
    def score_ceiling(self) -> float | None:
        """``-log10(epsilon)``: the largest matching score this run can emit.

        ``None`` when epsilon is unknown, and also when it is zero — a run
        configured without the stabiliser has no ceiling.
        """
        if self.epsilon is None or self.epsilon <= 0:
            return None
        return -math.log10(self.epsilon)

    def independence_threshold(self, reason: str | None) -> float | None:
        """The boundary the given independence reason was decided against.

        The reason names the zone; this maps it to that zone's lower bound, so
        a summary can say "exceeding the compelling threshold of 3.0" without
        assuming which zone applied. ``None`` for a reason that is not a rarity
        zone — a shared source or an explicit link is not a score comparison —
        and for any zone whose boundary this run did not record.
        """
        return {
            "rarity_compelling": self.lmatch_compelling,
            "rarity_intermediate": self.lmatch_intermediate,
            "rarity_limited": self.lmatch_limited,
            "rarity_insufficient": self.lmatch_limited,
        }.get(reason or "")


def _load_mapping(path: Path) -> dict:
    """Read a run manifest, YAML or JSON, chosen by suffix.

    ``yaml.safe_load`` only: a manifest is data, and the loader that can
    construct arbitrary Python objects has no business reading it.
    """
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        loaded = yaml.safe_load(text)
    else:
        loaded = json.loads(text)
    if not isinstance(loaded, dict):
        raise ValueError(
            f"{path}: expected a run manifest object, got a "
            f"{type(loaded).__name__}."
        )
    return loaded


def load_evidence_run_settings(path: str | Path) -> EvidenceRunSettings:
    """Read the decision settings out of a run manifest.

    Reads ``pipeline_config.epsilon`` and the ``L_match`` boundaries from
    ``decision_thresholds``. A manifest missing either section yields settings
    with those fields unset rather than an error — the review degrades to
    naming zones instead of quoting thresholds, which is the designed
    behaviour when a run's settings were not kept.
    """
    manifest_path = Path(path)
    raw = _load_mapping(manifest_path)

    values: dict[str, object] = {}
    pipeline = raw.get("pipeline_config")
    if isinstance(pipeline, dict):
        for key in _PIPELINE_KEYS:
            if key in pipeline:
                values[key] = pipeline[key]
    thresholds = raw.get("decision_thresholds")
    if isinstance(thresholds, dict):
        for key in _THRESHOLD_KEYS:
            if key in thresholds:
                values[key] = thresholds[key]

    for key in ("run_id", "created_at", "dev_git_commit"):
        if raw.get(key) is not None:
            values[key] = str(raw[key])
    dev_package = raw.get("dev_package")
    if isinstance(dev_package, dict) and dev_package.get("version") is not None:
        values["dev_package_version"] = str(dev_package["version"])

    return EvidenceRunSettings.model_validate(
        {**values, "source": str(manifest_path)}
    )


def page_provenance(
    settings: Iterable[EvidenceRunSettings | None],
) -> ProvenanceVM:
    """Footer provenance for a page built from these runs.

    The rendering version is always stated. A run's own identity is stated
    only when every case that has one agrees: a collection spanning two runs
    reports neither, and says so, rather than attributing the page to
    whichever happened to be first.
    """
    present = [s for s in settings if s is not None]
    if not present:
        return ProvenanceVM()

    def one(attr: str) -> str | None:
        """The single distinct value of ``attr``, or ``None`` if they differ."""
        values = {
            value
            for value in (getattr(s, attr) for s in present)
            if value not in (None, "")
        }
        return values.pop() if len(values) == 1 else None

    run_ids = {s.run_id for s in present if s.run_id}
    configurations = {s.configuration_text for s in present}
    if len(run_ids) > 1 or len(configurations) > 1:
        return ProvenanceVM(several_runs=True)

    return ProvenanceVM(
        dev_git_commit=one("dev_git_commit"),
        run_id=one("run_id"),
        run_created_at=one("created_at"),
        configuration=next(iter(configurations)) if configurations else "",
        source=one("source") or "",
    )


__all__ = [
    "EvidenceRunSettings",
    "load_evidence_run_settings",
    "page_provenance",
]
