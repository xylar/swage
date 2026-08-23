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
    #: Whether swage may act on this without a human looking. A guess from
    #: layer 4, or an answer to a question swage had to weaken to get one, is
    #: not exact -- and G2 is what turns that into a stop condition.
    exact: bool
    #: Extras the requirement asked for that ``conda_name`` does not carry.
    #: Non-empty only where nothing accounted for them, and ``exact`` is False
    #: whenever it is: the name resolved, but not the requirement that was
    #: asked (DESIGN.md 3.2). Recorded rather than merely flagged so G2 can
    #: name the extra and the two ways of accounting for it.
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
        #: Which layer is the PyPI-to-conda table. A name a reader already
        #: mapped is not a PyPI distribution name, so that layer is the one it
        #: must not be asked about -- while the config layers above it still
        #: apply, because `proj.4` saying it wants `sqlite` rather than
        #: `libsqlite` is a statement about this recipe whatever produced the
        #: name.
        self._pypi_source = pypi_source
        # A second view of each layer keyed by normalized name, so that an
        # upstream project that changes `Foo_Bar` to `foo-bar` does not quietly
        # stop matching the entry someone wrote for it.
        self._normalized: tuple[tuple[str, Mapping[str, str]], ...] = tuple(
            (layer.source, _normalize_keys(layer.entries)) for layer in name_map.layers
        )

    def resolve(self, pypi_name: str, mapped: bool = False) -> Resolution | None:
        """Resolve ``pypi_name``, or ``None`` if nothing can justify an answer.

        ``None`` is a result, not an error: it means no config entry covers
        this name and conda-forge has no package by it. The caller decides
        whether that stops the feedstock.

        ``mapped`` says a reader answered this already -- see
        `UpstreamMetadata.conda_names` -- so the PyPI table is skipped and
        everything else is asked as usual.
        """
        for candidate_source, conda_name in self._entries(pypi_name, mapped):
            return Resolution(pypi_name, conda_name, candidate_source, exact=True)

        # The spelling as written first, and the normalized one only after --
        # conda-forge does not normalize its own package names, so the channel
        # really does publish `kubernetes_asyncio` and `zope.interface` under
        # those names and nothing under the PEP 503 forms (DESIGN.md 3.6.1).
        # Asking only for the normalized name misses 2,163 underscore-named and
        # 544 dotted packages, and the symptom points somewhere else entirely:
        # a dependency conda-forge plainly has is reported as unresolvable at
        # G2, or worse, as coming from nowhere.
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
