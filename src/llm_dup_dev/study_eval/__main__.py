"""Entry point: ``python -m llm_dup_dev.study_eval``."""

from __future__ import annotations

import sys

from llm_dup_dev.study_eval.cli import main

if __name__ == "__main__":
    sys.exit(main())
