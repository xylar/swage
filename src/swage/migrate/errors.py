"""Errors raised when a v0 recipe cannot be converted."""

from __future__ import annotations

__all__ = ["MigrationError"]


class MigrationError(Exception):
    """A `meta.yaml` cannot be converted into a `recipe.yaml` swage can use.

    A fact about one feedstock, like `RecipeError` and `PlanError`, so a sweep
    turns it into a failed record rather than letting it stop the run.
    """
