"""Where swage keeps things it downloaded rather than computed.

One root for the whole tool, because two of them would drift: the run
directory and the name-resolution caches are both disposable and both want the
same "delete this and swage still works" property, and a user clearing one
expects to have cleared the other.

Everything under here is derivable again from the network, so nothing durable
lives in it -- that stays in git or in the feedstocks themselves (DESIGN.md 9).
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
