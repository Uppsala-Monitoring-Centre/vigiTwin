"""Entry point: ``python -m vigitwin.study_eval``."""

from __future__ import annotations

import sys

from vigitwin.study_eval.cli import main

if __name__ == "__main__":
    sys.exit(main())
