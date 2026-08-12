"""The layered, schema-validated quirks database (DESIGN.md 4)."""

from __future__ import annotations

from .errors import ConfigError
from .loader import (
    AddedRequirement,
    ConfigTree,
    FeedstockConfig,
    Layered,
    MappingLayer,
    find_config_root,
    load_config,
)
from .schema import (
    AddRequirements,
    Defaults,
    ExtrasAsOutputs,
    Family,
    Feedstock,
    GitHubUpstream,
    Output,
    OutputRun,
    PyPIUpstream,
    Quirks,
    RecipeOwned,
    RequiresPython,
    TrustLevel,
    Upstream,
)

__all__ = [
    "AddRequirements",
    "AddedRequirement",
    "ConfigError",
    "ConfigTree",
    "Defaults",
    "ExtrasAsOutputs",
    "Family",
    "Feedstock",
    "FeedstockConfig",
    "GitHubUpstream",
    "Layered",
    "MappingLayer",
    "Output",
    "OutputRun",
    "PyPIUpstream",
    "Quirks",
    "RecipeOwned",
    "RequiresPython",
    "TrustLevel",
    "Upstream",
    "find_config_root",
    "load_config",
]
