"""Turning upstream, config and a recipe into a plan (DESIGN.md 3.3)."""

from __future__ import annotations

from .attribute import (
    Attribution,
    AttributionIndex,
    Provenance,
    Unexplained,
    attribute,
    build_index,
)
from .errors import PlanError
from .lines import ParsedLine, parse_line
from .python_min import PythonMin, resolve_python_min
from .reconcile import Reconciled, reconcile
from .removals import Removal, classify_removal

__all__ = [
    "Attribution",
    "AttributionIndex",
    "ParsedLine",
    "PlanError",
    "Provenance",
    "PythonMin",
    "Reconciled",
    "Removal",
    "Unexplained",
    "attribute",
    "build_index",
    "classify_removal",
    "parse_line",
    "reconcile",
    "resolve_python_min",
]
