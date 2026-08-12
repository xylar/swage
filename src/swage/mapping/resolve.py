"""PyPI name to conda-forge name, with provenance (DESIGN.md 3.2).

This is the step most likely to be silently wrong, so every answer says where
it came from and whether it was exact. Trust gate G2 refuses to auto-merge a
feedstock whose plan contains an inexact or unresolved name -- which is what
turns "the tool guessed and I didn't notice" into a stop condition.

Resolution is layered, first match wins:

1. the feedstock's own ``name_map``
2. its family's ``name_map``
3. the global ``config/name-map.yaml``
4. identity -- the PyPI name, normalized, is a package conda-forge actually has
5. unresolved

Layer 4 is deliberately not "the names look the same". It asks whether the
package exists, because otherwise every unknown name would resolve to itself
and the unresolved state would be unreachable.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from swage.config import Layered

__all__ = [
    "NameResolver",
    "PackageIndex",
    "Resolution",
    "StaticPackageIndex",
    "normalize_name",
]

_SEPARATORS = re.compile(r"[-_.]+")

#: The source recorded for a name that resolved to itself.
IDENTITY = "identity"


def normalize_name(name: str) -> str:
    """PEP 503 normalization: lowercase, and runs of ``-_.`` become ``-``."""
    return _SEPARATORS.sub("-", name).lower()


class PackageIndex(Protocol):
    """Whether conda-forge has a package under a given name."""

    def has(self, conda_name: str) -> bool: ...


@dataclass(frozen=True)
class StaticPackageIndex:
    """A package index from a fixed set of names.

    Enough for tests and for a cached name list; a channel-backed
    implementation slots in behind the same protocol.
    """

    names: frozenset[str]

    @classmethod
    def of(cls, *names: str) -> StaticPackageIndex:
        return cls(frozenset(names))

    def has(self, conda_name: str) -> bool:
        return conda_name in self.names


@dataclass(frozen=True)
class Resolution:
    """What a PyPI name resolved to, and on what authority."""

    pypi_name: str
    conda_name: str
    source: str
    exact: bool


class NameResolver:
    """Resolves PyPI names for one feedstock."""

    def __init__(self, name_map: Layered[str], index: PackageIndex) -> None:
        self._name_map = name_map
        self._index = index
        # A second view of each layer keyed by normalized name, so that an
        # upstream project that changes `Foo_Bar` to `foo-bar` does not quietly
        # stop matching the entry someone wrote for it.
        self._normalized: tuple[tuple[str, Mapping[str, str]], ...] = tuple(
            (layer.source, _normalize_keys(layer.entries)) for layer in name_map.layers
        )

    def resolve(self, pypi_name: str) -> Resolution | None:
        """Resolve ``pypi_name``, or ``None`` if nothing can justify an answer.

        ``None`` is a result, not an error: it means no config entry covers
        this name and conda-forge has no package by it. The caller decides
        whether that stops the feedstock.
        """
        found = self._name_map.lookup(pypi_name)
        if found is not None:
            conda_name, source = found
            return Resolution(pypi_name, conda_name, source, exact=True)

        normalized = normalize_name(pypi_name)
        for source, entries in self._normalized:
            if normalized in entries:
                return Resolution(pypi_name, entries[normalized], source, exact=True)

        if self._index.has(normalized):
            return Resolution(pypi_name, normalized, IDENTITY, exact=True)
        return None


def _normalize_keys(entries: Mapping[str, str]) -> Mapping[str, str]:
    # First spelling wins, so an exact-cased entry is not shadowed by a later
    # one that happens to normalize the same way.
    normalized: dict[str, str] = {}
    for key, value in entries.items():
        normalized.setdefault(normalize_name(key), value)
    return normalized
