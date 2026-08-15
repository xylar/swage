"""The run record, and the renderings of it (DESIGN.md 9)."""

from __future__ import annotations

from .artifact import (
    RECIPES_DIR,
    RUN_FILE,
    latest_run,
    read_run,
    run_directory,
    runs_since,
    write_recipes,
    write_run,
)
from .build import build_record, compact, summarize_recipe
from .draft import (
    Workbench,
    config_draft,
    findings_markdown,
    render_workbench,
    write_workbench,
)
from .errors import ReportError
from .explain import render_explain
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

__all__ = [
    "OUTCOMES",
    "RECIPES_DIR",
    "RUN_FILE",
    "SCHEMA_VERSION",
    "CheckRecord",
    "FeedstockRecord",
    "GateRecord",
    "MergeCheckRecord",
    "Outcome",
    "PlannedLine",
    "ReportError",
    "RunRecord",
    "SectionRecord",
    "UpstreamRecord",
    "Workbench",
    "build_record",
    "compact",
    "config_draft",
    "findings_markdown",
    "latest_run",
    "read_run",
    "render_explain",
    "render_summary",
    "render_workbench",
    "run_directory",
    "runs_since",
    "summarize_recipe",
    "supports_color",
    "write_recipes",
    "write_run",
    "write_workbench",
]
