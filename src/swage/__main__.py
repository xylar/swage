"""Support ``python -m swage``."""

from __future__ import annotations

import sys

from swage.cli import main

if __name__ == "__main__":
    sys.exit(main())
