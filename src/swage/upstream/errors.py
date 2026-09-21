"""Errors raised when upstream metadata cannot be trusted."""

from __future__ import annotations

__all__ = ["NothingToReconcile", "UpstreamError"]


class UpstreamError(Exception):
    """Upstream metadata is missing, malformed, or not statically knowable.
    Raised at the boundary.
    """


class NothingToReconcile(Exception):
    """This feedstock packages nothing swage can read a declaration for.
    Deliberately not an `UpstreamError`: nothing has gone wrong.
    """
