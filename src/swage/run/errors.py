"""Errors raised when a run artifact cannot be read."""

from __future__ import annotations

__all__ = ["ReportError"]


class ReportError(Exception):
    """A run record is missing, malformed, or from a schema swage cannot read.
    Raised at the boundary: a record read back from disk is input.
    """
