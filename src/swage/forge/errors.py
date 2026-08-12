"""Errors raised when GitHub or an upstream archive cannot be read."""

from __future__ import annotations

__all__ = ["ForgeError"]


class ForgeError(Exception):
    """swage could not read what it went looking for.

    Raised at the boundary, where the answer comes from a network service
    rather than from swage's own code. Everything above this layer decides
    what a recipe should say from what this layer returns, so a wrong answer
    here is worse than no answer.
    """
