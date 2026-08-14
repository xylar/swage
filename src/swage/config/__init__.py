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
    ArchiveUpstream,
    Defaults,
    DynamicPolicy,
    ExtrasAsOutputs,
    Family,
    Feedstock,
    GitHubUpstream,
    Output,
    OutputRun,
    Quirks,
    RecipeOwned,
    RemovalPolicy,
    RunConstraint,
    TestMatrixPolicy,
    TrustLevel,
    Upstream,
)

__all__ = [
    "AddRequirements",
    "AddedRequirement",
    "ArchiveUpstream",
    "ConfigError",
    "ConfigTree",
    "Defaults",
    "DynamicPolicy",
    "ExtrasAsOutputs",
    "Family",
    "Feedstock",
    "FeedstockConfig",
    "GitHubUpstream",
    "Layered",
    "MappingLayer",
    "Output",
    "OutputRun",
    "Quirks",
    "RecipeOwned",
    "RemovalPolicy",
    "RunConstraint",
    "TestMatrixPolicy",
    "TrustLevel",
    "Upstream",
    "find_config_root",
    "load_config",
]
