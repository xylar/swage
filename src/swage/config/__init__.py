"""The layered, schema-validated quirks database (DESIGN.md 4)."""

from __future__ import annotations

from .errors import ConfigError
from .schema import (
    Defaults,
    ExtrasAsOutputs,
    Family,
    Feedstock,
    GitHubUpstream,
    Output,
    OutputRun,
    PyPIUpstream,
    Quirks,
    RequiresPython,
    TrustLevel,
    Upstream,
)

__all__ = [
    "ConfigError",
    "Defaults",
    "ExtrasAsOutputs",
    "Family",
    "Feedstock",
    "GitHubUpstream",
    "Output",
    "OutputRun",
    "PyPIUpstream",
    "Quirks",
    "RequiresPython",
    "TrustLevel",
    "Upstream",
]
