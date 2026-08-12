"""Name normalization shared by layers that must agree on a spelling.

The extras rule lives here rather than in `upstream` because the config layer
needs it too, and config sits below `upstream` -- it has no knowledge of the
layers above it (DESIGN.md 3). Two copies of a rule that has to match for a
config lookup to hit is exactly the kind of drift that fails silently.

`mapping.normalize_name` deliberately stays separate. It applies PEP 503 to
*package* names, which is a different question about a different thing, and
the two specs are free to diverge even though PEP 685 defers to PEP 503's
algorithm today.
"""

from __future__ import annotations

import re

__all__ = ["normalize_extra"]

_SEPARATORS = re.compile(r"[-_.]+")


def normalize_extra(name: str) -> str:
    """PEP 685 normalization: lowercase, and runs of ``-_.`` become ``-``.

    Build backends apply this when they write core metadata and nothing
    applies it to ``pyproject.toml``, so the same extra of the same release
    reaches swage spelled two ways -- `bigquery_v2` from one file and
    `bigquery-v2` from the other. Normalizing on read is what stops an extra's
    name depending on which file an sdist happened to ship.
    """
    return _SEPARATORS.sub("-", name).lower()
