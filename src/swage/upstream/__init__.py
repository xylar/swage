"""Upstream metadata, normalized (DESIGN.md 3)."""

from __future__ import annotations

from .errors import UpstreamError
from .model import UpstreamMetadata, UpstreamRequirement
from .pyproject import parse_pyproject, parse_requirement

__all__ = [
    "UpstreamError",
    "UpstreamMetadata",
    "UpstreamRequirement",
    "parse_pyproject",
    "parse_requirement",
]
