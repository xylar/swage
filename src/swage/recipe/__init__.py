"""The recipe.yaml model (DESIGN.md 3.1)."""

from __future__ import annotations

from .errors import RecipeError
from .model import (
    BlockContent,
    Recipe,
    RecipeOutput,
    Requirement,
    RequirementsBlock,
)
from .read import read_recipe, resolve_expression
from .render import render_block
from .write import render_recipe

__all__ = [
    "BlockContent",
    "Recipe",
    "RecipeError",
    "RecipeOutput",
    "Requirement",
    "RequirementsBlock",
    "read_recipe",
    "render_block",
    "render_recipe",
    "resolve_expression",
]
