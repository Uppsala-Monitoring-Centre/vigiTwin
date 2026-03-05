"""
Evaluation framework for the duplicate-detection pipeline.

Usage (from the secure repo)::

    from llm_dup_dev.eval import evaluate, summary_to_text

    summary = evaluate(pipeline_results, eval_annotations, experiment_id="baseline_v1")
    print(summary_to_text(summary))
"""

from .comparison import evaluate
from .metrics import ConfusionMatrix, EvalSummary
from .report import summary_to_markdown, summary_to_text

__all__ = [
    "evaluate",
    "EvalSummary",
    "ConfusionMatrix",
    "summary_to_text",
    "summary_to_markdown",
]
