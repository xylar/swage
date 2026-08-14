"""Errors raised when GitHub or an upstream archive cannot be read."""

from __future__ import annotations

__all__ = ["ForgeError", "NotFound"]


class ForgeError(Exception):
    """swage could not read what it went looking for.

    Raised at the boundary, where the answer comes from a network service
    rather than from swage's own code. Everything above this layer decides
    what a recipe should say from what this layer returns, so a wrong answer
    here is worse than no answer.

    ``said`` is the program's own stderr, kept apart from the argv that
    provoked it. A report line wants that half and only that half, and
    recovering it from the message by splitting on a newline worked only
    while every argv was one line: the first merge swage attempted passed a
    commit body through `--body`, so the split landed in the middle of the
    body and the report printed half a commit message. Carrying it costs one
    field and cannot be got wrong later.
    """

    def __init__(self, message: str, said: str = "") -> None:
        super().__init__(message)
        self.said = said


class NotFound(ForgeError):
    """It does not exist, as opposed to it could not be read.

    Its own type because callers act on the difference, and most of the time
    the absence is not a failure at all. A missing `recipe/recipe.yaml` means
    look for `meta.yaml` and route the feedstock to migration; a missing
    `.ci_support` means conda-smithy has never rendered this feedstock, and
    the planner says so rather than assuming a build floor. Reading "does not
    exist" back out of an error message at each call site is how one of those
    eventually gets read wrong.
    """
