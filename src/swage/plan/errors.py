"""Errors raised when a feedstock cannot be planned.

A `PlanError` is a stop, not a warning (v1 §3.3.2): the feedstock is reported
under FAILED with enough detail to act on.
"""

from __future__ import annotations

__all__ = ["PlanError"]


class PlanError(Exception):
    """swage cannot produce a plan it is willing to stand behind."""
