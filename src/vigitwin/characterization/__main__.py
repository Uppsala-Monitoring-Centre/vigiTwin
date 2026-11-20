"""Entry point: ``python -m vigitwin.characterization``."""

from __future__ import annotations

import sys

from vigitwin.characterization.cli import main

if __name__ == "__main__":
    sys.exit(main())
