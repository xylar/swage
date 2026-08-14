"""The recipe.yaml model (DESIGN.md 3.1)."""

from __future__ import annotations

from .errors import RecipeError
from .model import (
    BlockContent,
    Conditional,
    Entry,
    Recipe,
    RecipeOutput,
    RecipeSource,
    Requirement,
    RequirementsBlock,
)
from .read import read_recipe, resolve_expression
from .render import inline_text, render_block
from .write import render_recipe

__all__ = [
    "BlockContent",
    "Conditional",
    "Entry",
    "Recipe",
    "RecipeError",
    "RecipeOutput",
    "RecipeSource",
    "Requirement",
    "RequirementsBlock",
    "inline_text",
    "read_recipe",
    "render_block",
    "render_recipe",
    "resolve_expression",
]
