"""Where swage keeps things it downloaded rather than computed: one root,
everything under it derivable again from the network (v1 §9).
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["cache_root"]


def cache_root() -> Path:
    """The directory swage caches under, honoring ``XDG_CACHE_HOME``."""
    from_env = os.environ.get("XDG_CACHE_HOME")
    base = Path(from_env) if from_env else Path.home() / ".cache"
    return base / "swage"
