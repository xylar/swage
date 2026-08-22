"""Errors raised when a v0 recipe cannot be converted."""

from __future__ import annotations

__all__ = ["MigrationError"]


class MigrationError(Exception):
    """A `meta.yaml` cannot be converted into a `recipe.yaml` swage can use.

    A fact about one feedstock, like `RecipeError` and `PlanError`, so a sweep
    turns it into a failed record rather than letting it stop the run.

    ``summary`` is the one line a fleet report has room for. The message
    itself opens by naming the feedstock, which a report has just named in the
    column beside it, so taking its first line would spend that room saying
    "the converter cannot read this recipe" and leave out which part of it
    could not be read.
    """

    def __init__(self, message: str, summary: str = "") -> None:
        super().__init__(message)
        self.summary = summary or message.splitlines()[0]
