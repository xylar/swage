"""The run record, and the renderings of it (DESIGN.md 9)."""

from __future__ import annotations

from .artifact import (
    DECLARATIONS_DIR,
    RECIPES_DIR,
    RUN_FILE,
    all_runs,
    latest_run,
    read_run,
    run_directory,
    runs_since,
    write_declarations,
    write_recipes,
    write_run,
)
from .build import (
    build_record,
    compact,
    declaration_diff,
    summarize_recipe,
    was_shortened,
)
from .draft import (
    Workbench,
    config_draft,
    findings_markdown,
    render_family,
    render_workbench,
    write_workbench,
)
from .errors import ReportError
from .explain import render_explain
from .migrate import condition_rows, render_migration, render_refusal
from .model import (
    OUTCOMES,
    SCHEMA_VERSION,
    CheckRecord,
    FeedstockRecord,
    GateRecord,
    MergeCheckRecord,
    Outcome,
    PlannedLine,
    RunRecord,
    SectionRecord,
    UpstreamRecord,
)
from .terminal import render_summary, supports_color
from .trust import (
    TRUST_READINGS,
    Earned,
    FleetState,
    earned,
    fleet_states,
    render_trust,
)

__all__ = [
    "DECLARATIONS_DIR",
    "OUTCOMES",
    "RECIPES_DIR",
    "RUN_FILE",
    "SCHEMA_VERSION",
    "TRUST_READINGS",
    "CheckRecord",
    "Earned",
    "FeedstockRecord",
    "FleetState",
    "GateRecord",
    "MergeCheckRecord",
    "Outcome",
    "PlannedLine",
    "ReportError",
    "RunRecord",
    "SectionRecord",
    "UpstreamRecord",
    "Workbench",
    "all_runs",
    "build_record",
    "compact",
    "condition_rows",
    "config_draft",
    "declaration_diff",
    "earned",
    "findings_markdown",
    "fleet_states",
    "latest_run",
    "read_run",
    "render_explain",
    "render_family",
    "render_migration",
    "render_refusal",
    "render_summary",
    "render_trust",
    "render_workbench",
    "run_directory",
    "runs_since",
    "summarize_recipe",
    "supports_color",
    "was_shortened",
    "write_declarations",
    "write_recipes",
    "write_run",
    "write_workbench",
]
