"""
Evaluation framework for the duplicate-detection pipeline.

Usage (legacy dict interface, from the secure repo)::

    from llm_dup_dev.eval import evaluate, summary_to_text

    summary = evaluate(pipeline_results, eval_annotations, experiment_id="baseline_v1")
    print(summary_to_text(summary))

Usage (new typed interface)::

    from llm_dup_dev.eval import evaluate_batch, summary_to_text, AbstainMode

    summary = evaluate_batch(
        batch_result, eval_annotations,
        experiment_id="v2",
        abstain_mode=AbstainMode.DECIDED_ONLY,
    )
    print(summary_to_text(summary))

Usage (offline threshold calibration)::

    from llm_dup_dev.eval import (
        AbstainMode, grid_over, load_annotations, sweep, sweep_to_text,
    )
    from llm_dup_dev.pipeline.export import read_pair_features

    features = read_pair_features("features.json")
    annotations = load_annotations("annotations.csv")
    result = sweep(
        features, annotations,
        grid=grid_over(lpatient_compelling=[1.5, 2.0, 2.5, 3.0]),
        abstain_mode=AbstainMode.DECIDED_ONLY,
    )
    print(sweep_to_text(result))
"""

from .annotations import derive_pair_id, load_annotations
from .bridge import evaluate_batch, evaluate_results, to_eval_dict
from .comparison import evaluate
from .labels import AbstainMode
from .metrics import BinaryMetrics, ConfusionMatrix, EvalSummary
from .report import (
    summary_to_markdown,
    summary_to_text,
    sweep_to_markdown,
    sweep_to_text,
)
from .sweep import Coverage, SweepPoint, SweepResult, check_coverage, grid_over, sweep

__all__ = [
    # Legacy interface
    "evaluate",
    # New typed interface
    "evaluate_batch",
    "evaluate_results",
    "to_eval_dict",
    # Abstain mode control
    "AbstainMode",
    # Metrics
    "EvalSummary",
    "BinaryMetrics",
    "ConfusionMatrix",
    # Annotations
    "load_annotations",
    "derive_pair_id",
    # Threshold calibration
    "sweep",
    "grid_over",
    "check_coverage",
    "SweepResult",
    "SweepPoint",
    "Coverage",
    # Reporting
    "summary_to_text",
    "summary_to_markdown",
    "sweep_to_text",
    "sweep_to_markdown",
]
