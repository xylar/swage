"""Upstream metadata, normalized (design-v1.md 3)."""

from __future__ import annotations

from .errors import NothingToReconcile, UpstreamError
from .metadata import parse_metadata
from .model import (
    EntryPoint,
    RecipeUpstream,
    UpstreamMetadata,
    UpstreamRequirement,
    normalize_extra,
)
from .pyproject import (
    parse_build_requires,
    parse_entry_points,
    parse_entry_points_txt,
    parse_pyproject,
    parse_requirement,
)

__all__ = [
    "EntryPoint",
    "NothingToReconcile",
    "RecipeUpstream",
    "UpstreamError",
    "UpstreamMetadata",
    "UpstreamRequirement",
    "normalize_extra",
    "parse_build_requires",
    "parse_entry_points",
    "parse_entry_points_txt",
    "parse_metadata",
    "parse_pyproject",
    "parse_requirement",
]
