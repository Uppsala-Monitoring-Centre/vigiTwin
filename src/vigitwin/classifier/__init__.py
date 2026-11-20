"""Classifier module — decision logic and configurable thresholds.

Submodules:
    config          Configurable thresholds for the pipeline and classifier.
    decision_logic  Rule-based classification of report pairs.
"""

from vigitwin.classifier.config import DecisionThresholds, PipelineConfig
from vigitwin.classifier.decision_logic import classify

__all__ = ["DecisionThresholds", "PipelineConfig", "classify"]
