"""Name normalization shared by layers that must agree on a spelling.

The extras rule lives here because `config` needs it and sits below `upstream`
(DESIGN.md §4). `mapping.normalize_name` stays separate: PEP 503 on package
names is a different question.
"""

from __future__ import annotations

import re

__all__ = ["normalize_extra"]

_SEPARATORS = re.compile(r"[-_.]+")


def normalize_extra(name: str) -> str:
    """PEP 685 normalization: lowercase, and runs of ``-_.`` become ``-``. Build
    backends apply it to core metadata and nothing applies it to
    ``pyproject.toml``, so it is applied on read.
    """
    return _SEPARATORS.sub("-", name).lower()
