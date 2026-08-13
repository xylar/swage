"""The run record, and the renderings of it (DESIGN.md 9)."""

from __future__ import annotations

from .artifact import (
    RECIPES_DIR,
    RUN_FILE,
    latest_run,
    read_run,
    run_directory,
    write_recipes,
    write_run,
)
from .build import build_record, summarize_recipe
from .errors import ReportError
from .explain import render_explain
from .model import (
    OUTCOMES,
    SCHEMA_VERSION,
    FeedstockRecord,
    GateRecord,
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
    "FeedstockRecord",
    "GateRecord",
    "Outcome",
    "PlannedLine",
    "ReportError",
    "RunRecord",
    "SectionRecord",
    "UpstreamRecord",
    "build_record",
    "latest_run",
    "read_run",
    "render_explain",
    "render_summary",
    "run_directory",
    "summarize_recipe",
    "supports_color",
    "write_recipes",
    "write_run",
]
