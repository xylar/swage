"""The run record, and the two renderings of it (DESIGN.md 9)."""

from __future__ import annotations

from .artifact import RUN_FILE, read_run, run_directory, write_run
from .errors import ReportError
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
    "read_run",
    "render_summary",
    "run_directory",
    "supports_color",
    "write_run",
]
