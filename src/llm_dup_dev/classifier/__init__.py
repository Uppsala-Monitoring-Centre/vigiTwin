"""Classifier module — decision logic and configurable thresholds.

Submodules:
    config          Configurable thresholds for the pipeline and classifier.
    decision_logic  Rule-based classification of report pairs.
"""

from llm_dup_dev.classifier.config import DecisionThresholds, PipelineConfig
from llm_dup_dev.classifier.decision_logic import classify

__all__ = ["DecisionThresholds", "PipelineConfig", "classify"]
