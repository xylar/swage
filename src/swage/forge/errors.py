"""Errors raised when GitHub or an upstream archive cannot be read."""

from __future__ import annotations

__all__ = ["ForgeError", "NotFound"]


class ForgeError(Exception):
    """swage could not read what it went looking for, raised at the boundary.

    ``said`` is the program's own stderr, kept apart from the argv that
    provoked it, for the report line.
    """

    def __init__(self, message: str, said: str = "") -> None:
        super().__init__(message)
        self.said = said


class NotFound(ForgeError):
    """It does not exist, as opposed to it could not be read: its own type
    because callers act on the difference.
    """
