"""
Evaluation framework for the duplicate-detection pipeline.

Usage (legacy dict interface, from the secure repo)::

    from llm_dup_dev.eval import evaluate, summary_to_text

    summary = evaluate(pipeline_results, eval_annotations, experiment_id="baseline_v1")
    print(summary_to_text(summary))

Usage (new typed interface)::

    from llm_dup_dev.eval import evaluate_batch, summary_to_text

    summary = evaluate_batch(batch_result, eval_annotations, experiment_id="v2")
    print(summary_to_text(summary))
"""

from .bridge import evaluate_batch, evaluate_results, to_eval_dict
from .comparison import evaluate
from .metrics import ConfusionMatrix, EvalSummary
from .report import summary_to_markdown, summary_to_text

__all__ = [
    # Legacy interface
    "evaluate",
    # New typed interface
    "evaluate_batch",
    "evaluate_results",
    "to_eval_dict",
    # Metrics
    "EvalSummary",
    "ConfusionMatrix",
    # Reporting
    "summary_to_text",
    "summary_to_markdown",
]
