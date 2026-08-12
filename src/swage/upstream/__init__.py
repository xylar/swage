"""Upstream metadata, normalized (DESIGN.md 3)."""

from __future__ import annotations

from .errors import UpstreamError
from .metadata import parse_metadata
from .model import UpstreamMetadata, UpstreamRequirement, normalize_extra
from .pyproject import parse_pyproject, parse_requirement

__all__ = [
    "UpstreamError",
    "UpstreamMetadata",
    "UpstreamRequirement",
    "normalize_extra",
    "parse_metadata",
    "parse_pyproject",
    "parse_requirement",
]
