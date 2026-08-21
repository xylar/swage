"""Errors raised when upstream metadata cannot be trusted."""

from __future__ import annotations

__all__ = ["NothingToReconcile", "UpstreamError"]


class UpstreamError(Exception):
    """Upstream metadata is missing, malformed, or not statically knowable.

    Raised at the boundary. Everything swage decides about a recipe follows
    from this metadata, so a bad answer here is worse than no answer.
    """


class NothingToReconcile(Exception):
    """This feedstock packages nothing swage can read a declaration for.

    Deliberately not an `UpstreamError`: nothing has gone wrong. The feedstock
    builds something whose dependencies are declared somewhere swage has no
    reader for -- a CMakeLists.txt, a configure script, the import statements
    of a script -- and its config records that. A caller reports it and moves
    on rather than treating it as a failure to be fixed.
    """
