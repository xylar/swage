"""The recipe.yaml model (DESIGN.md 3.1)."""

from __future__ import annotations

from .errors import RecipeError
from .model import BlockContent, Requirement

__all__ = ["BlockContent", "RecipeError", "Requirement"]
