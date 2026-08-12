"""Turning upstream, config and a recipe into a plan (DESIGN.md 3.3)."""

from __future__ import annotations

from .assemble import (
    PlannedSection,
    RecipePlan,
    output_roles,
    plan_recipe,
    plan_section,
)
from .attribute import (
    Attribution,
    AttributionIndex,
    Provenance,
    Unexplained,
    attribute,
    build_index,
)
from .constrained import UnassociatedConstraint, check_run_constraints
from .errors import PlanError
from .lines import ParsedLine, parse_line
from .model import PlannedRequirement
from .order import order_requirements
from .preconditions import check_preconditions
from .python_min import PythonMin, resolve_python_min
from .reconcile import Reconciled, reconcile
from .removals import Removal, classify_removal

__all__ = [
    "Attribution",
    "AttributionIndex",
    "ParsedLine",
    "PlanError",
    "PlannedRequirement",
    "PlannedSection",
    "Provenance",
    "PythonMin",
    "RecipePlan",
    "Reconciled",
    "Removal",
    "UnassociatedConstraint",
    "Unexplained",
    "attribute",
    "build_index",
    "check_preconditions",
    "check_run_constraints",
    "classify_removal",
    "order_requirements",
    "output_roles",
    "parse_line",
    "plan_recipe",
    "plan_section",
    "reconcile",
    "resolve_python_min",
]
