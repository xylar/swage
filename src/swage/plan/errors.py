"""Errors raised when a feedstock cannot be planned.

A `PlanError` is a stop, not a warning. Across a few hundred feedstocks a
warning is a message nobody reads (DESIGN.md 3.3.2), so anything swage cannot
answer honestly raises here and the feedstock is reported under FAILED with
enough detail to act on without re-deriving it.
"""

from __future__ import annotations

__all__ = ["PlanError"]


class PlanError(Exception):
    """swage cannot produce a plan it is willing to stand behind."""
