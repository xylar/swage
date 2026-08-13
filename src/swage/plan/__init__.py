"""Turning upstream, config and a recipe into a plan (DESIGN.md 3.3)."""

from __future__ import annotations

from .assemble import (
    PlannedSection,
    RecipePlan,
    accounted_extras,
    declares_skip,
    output_roles,
    plan_recipe,
    plan_section,
    planned_blocks,
    planned_matrices,
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
from .gates import GateResult, Verdict, evaluate_gates
from .lines import ParsedLine, parse_line
from .model import PlannedConditional, PlannedEntry, PlannedRequirement, first_name
from .order import order_requirements
from .preconditions import check_preconditions
from .python_min import (
    PythonMin,
    check_upstream_floor,
    needs_python_min,
    resolve_python_min,
)
from .reconcile import Reconciled, reconcile
from .removals import Removal, classify_removal
from .test_matrix import TestMatrix, plan_test_matrices

__all__ = [
    "Attribution",
    "AttributionIndex",
    "GateResult",
    "ParsedLine",
    "PlanError",
    "PlannedConditional",
    "PlannedEntry",
    "PlannedRequirement",
    "PlannedSection",
    "Provenance",
    "PythonMin",
    "RecipePlan",
    "Reconciled",
    "Removal",
    "TestMatrix",
    "UnassociatedConstraint",
    "Unexplained",
    "Verdict",
    "accounted_extras",
    "attribute",
    "build_index",
    "check_preconditions",
    "check_run_constraints",
    "check_upstream_floor",
    "classify_removal",
    "declares_skip",
    "evaluate_gates",
    "first_name",
    "needs_python_min",
    "order_requirements",
    "output_roles",
    "parse_line",
    "plan_recipe",
    "plan_section",
    "plan_test_matrices",
    "planned_blocks",
    "planned_matrices",
    "reconcile",
    "resolve_python_min",
]
