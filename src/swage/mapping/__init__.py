"""PyPI to conda-forge name resolution, with provenance (DESIGN.md 3.2)."""

from __future__ import annotations

from .resolve import (
    IDENTITY,
    NameResolver,
    PackageIndex,
    Resolution,
    StaticPackageIndex,
    normalize_name,
)

__all__ = [
    "IDENTITY",
    "NameResolver",
    "PackageIndex",
    "Resolution",
    "StaticPackageIndex",
    "normalize_name",
]
