"""PyPI name to conda-forge name, with provenance (v1 §3.2; DESIGN.md §8).

Layered, first match wins: the feedstock's ``name_map``, its family's, the
global one, identity where conda-forge has the package, unresolved. Every answer
says where it came from and whether it was exact, which is what G2 reads.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
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
    """A package index from a fixed set of names, for tests and for a cached
    name list.
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
    #: Whether swage may act on this without a human looking: G2 turns a guess
    #: into a stop.
    exact: bool
    #: Extras the requirement asked for that ``conda_name`` does not carry;
    #: ``exact`` is False whenever this is non-empty (v1 §3.2).
    dropped_extras: tuple[str, ...] = ()


class NameResolver:
    """Resolves PyPI names for one feedstock."""

    def __init__(
        self,
        name_map: Layered[str],
        index: PackageIndex,
        pypi_source: str | None = None,
    ) -> None:
        self._name_map = name_map
        self._index = index
        #: Which layer is the PyPI-to-conda table, which a name a reader already
        #: mapped must not be asked about; the config layers above it still
        #: apply.
        self._pypi_source = pypi_source
        # A second view of each layer keyed by normalized name, so a renamed
        # upstream project keeps matching its entry.
        self._normalized: tuple[tuple[str, Mapping[str, str]], ...] = tuple(
            (layer.source, _normalize_keys(layer.entries)) for layer in name_map.layers
        )

    def resolve(self, pypi_name: str, mapped: bool = False) -> Resolution | None:
        """Resolve ``pypi_name``, or ``None`` if nothing can justify an answer.

        ``None`` is a result, not an error. ``mapped`` says a reader answered
        this already (`UpstreamMetadata.conda_names`), so the PyPI table is
        skipped.
        """
        for candidate_source, conda_name in self._entries(pypi_name, mapped):
            return Resolution(pypi_name, conda_name, candidate_source, exact=True)

        # The spelling as written first, and the normalized one after:
        # conda-forge does not normalize its own package names (v1 §3.6.1).
        for candidate in (pypi_name, normalize_name(pypi_name)):
            if self._index.has(candidate):
                return Resolution(pypi_name, candidate, IDENTITY, exact=True)
        return None

    def _entries(self, pypi_name: str, mapped: bool) -> Iterator[tuple[str, str]]:
        """Every config answer for this name, most specific first."""
        for layer in self._name_map.layers:
            if mapped and layer.source == self._pypi_source:
                continue
            if pypi_name in layer.entries:
                yield layer.source, layer.entries[pypi_name]
        normalized = normalize_name(pypi_name)
        for source, entries in self._normalized:
            if mapped and source == self._pypi_source:
                continue
            if normalized in entries:
                yield source, entries[normalized]


def _normalize_keys(entries: Mapping[str, str]) -> Mapping[str, str]:
    # First spelling wins, so an exact-cased entry is not shadowed by a later
    # one that happens to normalize the same way.
    normalized: dict[str, str] = {}
    for key, value in entries.items():
        normalized.setdefault(normalize_name(key), value)
    return normalized
