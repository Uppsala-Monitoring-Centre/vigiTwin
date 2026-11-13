"""Offline threshold calibration: re-decide stored features under many thresholds.

``classify`` is a pure function of ``(PairFeatures, DecisionThresholds)`` — no
I/O, no LLM, no randomness — and ``PairFeatures`` round-trips through JSON
losslessly. So once a batch has been run and its features persisted (see
:mod:`llm_dup_dev.pipeline.export`), the decision can be replayed under any
number of threshold settings for free. That is what makes
``duplicate_adjudication_spec.md § Parameter tuning`` — *"these parameters may
be refined during method development using annotated report pairs"* — actually
executable.

Scope: this sweeps :class:`DecisionThresholds` only. Those are downstream of all
evidence. ``PipelineConfig`` (domain sizes, inconsistency thresholds) is
*upstream* — it changes ``p_f`` and therefore ``L_match`` / ``L_patient`` — so
sweeping it would require re-running feature generation against reports and a
stats provider, which is not offline work.

This module reports; it does not choose. See :func:`sweep`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from llm_dup_dev.classifier.config import DecisionThresholds
from llm_dup_dev.classifier.decision_logic import classify
from llm_dup_dev.models.pair_features import PairFeatures

from .comparison import evaluate
from .labels import AbstainMode
from .metrics import EvalSummary


@dataclass
class Coverage:
    """How well the annotations line up with the stored features.

    Computed once per sweep, because every point scores the same pairs.

    ``evaluate`` skips an annotation whose pair_id is absent from the results
    without counting it, so a mismatched file yields a confident-looking score
    over a handful of pairs. This makes that visible.

    Pair ids on both sides are canonical (see
    :mod:`llm_dup_dev.models.pair_identity`), so a pair listed the other way
    round in the annotation file is no longer a way to match nothing. A stored
    features file written *before* canonicalisation still can be, though: its
    keys may be in the old orientation, and those annotations then show up in
    ``unmatched_annotations``. The fix in that case is to regenerate the
    features file, not to edit the annotations.
    """

    n_annotations: int
    n_features: int
    matched: list[str] = field(default_factory=list)
    #: Annotated pairs with no stored features.
    unmatched_annotations: list[str] = field(default_factory=list)
    #: Stored features with no annotation — unscored, not wrong.
    unannotated_features: list[str] = field(default_factory=list)

    @property
    def n_matched(self) -> int:
        return len(self.matched)


@dataclass
class SweepPoint:
    """One threshold setting and what it scored."""

    thresholds: DecisionThresholds
    summary: EvalSummary

    @property
    def n_pairs(self) -> int:
        """Pairs actually scored. Moves with abstain_mode — see :func:`sweep`."""
        return self.summary.n_pairs

    @property
    def n_abstained(self) -> int:
        """Pairs the pipeline abstained on at this setting."""
        return self.summary.n_pipeline_abstained


@dataclass
class SweepResult:
    """The full sweep: one point per valid threshold setting, plus diagnostics."""

    points: list[SweepPoint] = field(default_factory=list)
    coverage: Coverage | None = None
    abstain_mode: AbstainMode = AbstainMode.DECIDED_ONLY
    #: Grid points rejected by DecisionThresholds' own validator, with the reason.
    skipped: list[tuple[dict[str, Any], str]] = field(default_factory=list)


def check_coverage(
    features: dict[str, PairFeatures],
    annotations: Sequence[dict[str, Any]],
) -> Coverage:
    """Report how the annotations line up with the stored features.

    Does not mutate either side — it reports, and the caller decides.
    """
    annotated_ids = {a["pair_id"] for a in annotations}
    matched, unmatched = [], []

    for pair_id in sorted(annotated_ids):
        if pair_id in features:
            matched.append(pair_id)
        else:
            unmatched.append(pair_id)

    return Coverage(
        n_annotations=len(annotations),
        n_features=len(features),
        matched=matched,
        unmatched_annotations=unmatched,
        unannotated_features=sorted(set(features) - annotated_ids),
    )


def sweep(
    features: dict[str, PairFeatures],
    annotations: Sequence[dict[str, Any]],
    grid: Iterable[DecisionThresholds],
    abstain_mode: AbstainMode,
) -> SweepResult:
    """Score every threshold setting in ``grid`` against the same stored features.

    Parameters
    ----------
    features : dict[str, PairFeatures]
        Stored pipeline output, keyed by pair_id. See
        :func:`llm_dup_dev.pipeline.export.read_pair_features`.
    annotations : Sequence[dict]
        Gold labels. See :func:`llm_dup_dev.eval.annotations.load_annotations`.
    grid : Iterable[DecisionThresholds]
        The settings to try. Points rejected by ``DecisionThresholds``' own
        validator (it requires ``w_decisive`` to exceed every zone threshold)
        are recorded in ``SweepResult.skipped`` rather than aborting the sweep,
        so a rectangular grid may be passed without pre-filtering.
    abstain_mode : AbstainMode
        **Required, deliberately.** Under ``DECIDED_ONLY`` an abstained pair
        leaves ``n_pairs`` entirely, so a setting that abstains on the pairs it
        would have got wrong scores *better* on a smaller denominator. Choosing
        this inattentively is the difference between a calibration and a
        confidently wrong one.

    Returns
    -------
    SweepResult
        One :class:`SweepPoint` per valid setting, plus :class:`Coverage`.

    Notes
    -----
    **No winner is picked, by design.** There is no ``best`` and no ``argmax``:
    F1 across points is not comparable under ``DECIDED_ONLY`` because the
    denominator moves with the abstain count, so an automatic maximum would
    reliably select the setting that refuses the most pairs. Every point reports
    ``n_pairs`` alongside ``n_abstained`` so the trade is visible, and the
    judgement stays with the reader.
    """
    coverage = check_coverage(features, annotations)
    result = SweepResult(coverage=coverage, abstain_mode=abstain_mode)

    for thresholds in grid:
        try:
            # Re-validate: a caller may have built the grid from raw dicts, and
            # w_decisive must exceed every zone threshold.
            thresholds = DecisionThresholds.model_validate(
                thresholds.model_dump()
                if isinstance(thresholds, DecisionThresholds)
                else thresholds
            )
        except ValidationError as exc:
            raw = (
                thresholds.model_dump()
                if isinstance(thresholds, DecisionThresholds)
                else dict(thresholds)
            )
            result.skipped.append((raw, _validation_reason(exc)))
            continue

        classifications = [
            classify(f, thresholds) for f in features.values()
        ]
        summary = evaluate(
            pipeline_results=[
                {"pair_id": c.pair_id, "final_label": c.final_label}
                for c in classifications
            ],
            eval_annotations=list(annotations),
            experiment_id=_point_label(thresholds),
            # The snapshot was an untyped, unread dict; carrying the real
            # thresholds makes each point self-describing.
            config_snapshot=thresholds.model_dump(mode="json"),
            abstain_mode=abstain_mode,
        )
        result.points.append(SweepPoint(thresholds=thresholds, summary=summary))

    return result


def _validation_reason(exc: ValidationError) -> str:
    """Extract the constraint message, not pydantic's full report.

    A skipped grid point is only useful if the reader can see *why*, so pull
    out the validator's own sentence rather than the wrapper and its URL.
    """
    msgs = [
        e.get("msg", "").removeprefix("Value error, ")
        for e in exc.errors()
    ]
    return "; ".join(m for m in msgs if m) or str(exc)


def _point_label(thresholds: DecisionThresholds) -> str:
    """Name a sweep point by whatever it changed from the defaults."""
    defaults = DecisionThresholds().model_dump()
    changed = {
        k: v for k, v in thresholds.model_dump().items() if defaults[k] != v
    }
    if not changed:
        return "defaults"
    return ", ".join(f"{k}={v:g}" for k, v in sorted(changed.items()))


def grid_over(
    base: DecisionThresholds | None = None,
    **axes: Sequence[float],
) -> list[DecisionThresholds]:
    """Build a rectangular grid varying one or more threshold fields.

    ``grid_over(lmatch_limited=[0.5, 1.0, 1.5])`` yields three settings with
    every other field at its base value.

    Settings the validator rejects are **not** filtered here — :func:`sweep`
    records them in ``skipped`` with the reason, which is more informative than
    them quietly never appearing.

    Note the inconsistency weights (``w_minor`` … ``w_strong``) and the
    ``w_tot_*_zone`` thresholds are not independent: only their ratios matter,
    so sweeping both jointly explores a redundant space. Vary one side.
    """
    import itertools

    base = base or DecisionThresholds()
    if not axes:
        return [base]

    names = sorted(axes)
    out: list[DecisionThresholds] = []
    for combo in itertools.product(*(axes[n] for n in names)):
        try:
            out.append(base.model_copy(update=dict(zip(names, combo, strict=True))))
        except ValidationError:
            # model_copy does not re-validate; kept for safety if that changes.
            continue
    return out
