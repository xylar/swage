"""Errors raised when upstream metadata cannot be trusted."""

from __future__ import annotations

__all__ = ["UpstreamError"]


class UpstreamError(Exception):
    """Upstream metadata is missing, malformed, or not statically knowable.

    Raised at the boundary. Everything swage decides about a recipe follows
    from this metadata, so a bad answer here is worse than no answer.
    """
