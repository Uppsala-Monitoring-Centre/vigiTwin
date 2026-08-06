"""Entry point: ``python -m llm_dup_dev.characterization``."""

from __future__ import annotations

import sys

from llm_dup_dev.characterization.cli import main

if __name__ == "__main__":
    sys.exit(main())
