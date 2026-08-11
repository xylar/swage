"""Errors raised when a recipe is not shaped the way swage can work with."""

from __future__ import annotations

__all__ = ["RecipeError"]


class RecipeError(Exception):
    """A recipe cannot be read, or cannot be written back safely.

    Raised at the boundary -- reading a real recipe.yaml, or building
    requirements from upstream metadata -- rather than between swage's own
    layers.
    """
