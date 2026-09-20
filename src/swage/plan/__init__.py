"""Turning upstream, config and a recipe into a plan (design-v1.md 3.3)."""

from __future__ import annotations

from .assemble import (
    PlannedSection,
    RecipePlan,
    SelfConflict,
    accounted_extras,
    declares_skip,
    plan_recipe,
    plan_section,
    planned_blocks,
    planned_entry_points,
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
from .decision import Action, Ci, Decision, Outcome, decide, rung_sentence
from .entry_points import EntryPointChange, plan_entry_points
from .errors import PlanError
from .findings import (
    CHECKS,
    Check,
    Finding,
    Kind,
    by_kind,
    check,
    find,
    summarize,
    withheld,
)
from .grid import Artifacts, Branch, Reconciled, Universe, reconcile
from .lines import ParsedLine, parse_line, spec_key
from .model import PlannedConditional, PlannedEntry, PlannedRequirement, first_name
from .order import order_requirements
from .output import Output, derive_outputs, output_roles
from .preconditions import check_preconditions
from .python_min import (
    PythonMin,
    builds_per_python,
    check_upstream_floor,
    needs_python_min,
    resolve_python_min,
)
from .removals import Removal, classify_removal
from .test_matrix import TestMatrix, plan_test_matrices

__all__ = [
    "CHECKS",
    "Action",
    "Artifacts",
    "Attribution",
    "AttributionIndex",
    "Branch",
    "Check",
    "Ci",
    "Decision",
    "EntryPointChange",
    "Finding",
    "Kind",
    "Outcome",
    "Output",
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
    "SelfConflict",
    "TestMatrix",
    "UnassociatedConstraint",
    "Unexplained",
    "Universe",
    "accounted_extras",
    "attribute",
    "build_index",
    "builds_per_python",
    "by_kind",
    "check",
    "check_preconditions",
    "check_run_constraints",
    "check_upstream_floor",
    "classify_removal",
    "decide",
    "declares_skip",
    "derive_outputs",
    "find",
    "first_name",
    "needs_python_min",
    "order_requirements",
    "output_roles",
    "parse_line",
    "plan_entry_points",
    "plan_recipe",
    "plan_section",
    "plan_test_matrices",
    "planned_blocks",
    "planned_entry_points",
    "planned_matrices",
    "reconcile",
    "resolve_python_min",
    "rung_sentence",
    "spec_key",
    "summarize",
    "withheld",
]
