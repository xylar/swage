"""Order a requirements section the way swage writes it (DESIGN.md §9.6).

Recipe-owned first; then upstream's lines in upstream's source order; then
conda-forge-only additions, alphabetized. Sorting is stable, so anything the
rules do not distinguish keeps the order it arrived in.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .attribute import KEPT_UNEXPLAINED
from .model import PlannedConditional, PlannedEntry

__all__ = ["order_requirements"]

#: Buckets, in the order they are written.
_PYTHON_AND_PIP = 0
_OTHER_STRUCTURE = 1
_UPSTREAM = 2
_CONDA_FORGE_ONLY = 3

#: Within the first bucket, `python` precedes `pip`.
_FIRST = ("python", "pip")


def order_requirements(
    entries: Sequence[PlannedEntry], upstream_order: Mapping[str, int]
) -> tuple[PlannedEntry, ...]:
    """Sort one section's requirements into the order swage writes them.

    ``upstream_order`` maps a conda name to its position in upstream's
    declaration order (`AttributionIndex.order`).
    """
    return tuple(sorted(entries, key=lambda entry: _key(entry, upstream_order)))


def _key(
    entry: PlannedEntry, upstream_order: Mapping[str, int]
) -> tuple[int, int, str]:
    name = entry.name

    if isinstance(entry, PlannedConditional) and entry.preserved:
        # A conditional swage can explain sits where the dependency it states
        # sits: its condition is a blessed build variant (v1 §3.3.4).
        explained = entry.provenance.mapping
        if explained is not None:
            inherited = upstream_order.get(explained.conda_name)
            if inherited is not None:
                return (_UPSTREAM, inherited, "")
        # Anything else is structure swage does not understand and keeps the
        # order it arrived in.
        return (_OTHER_STRUCTURE, 0, "")

    if entry.provenance.origin == "recipe-kept":
        if entry.provenance.detail == KEPT_UNEXPLAINED:
            # Not structure, whatever the origin says: `recipe-kept` on a line
            # swage could not explain is a placeholder (v1 §3.3.6), and the line
            # trails with the conda-forge-only additions it joins once
            # documented.
            return (_CONDA_FORGE_ONLY, 0, name)
        if name in _FIRST:
            return (_PYTHON_AND_PIP, _FIRST.index(name), "")
        # Everything else structural keeps its incoming order.
        return (_OTHER_STRUCTURE, 0, "")

    if entry.provenance.origin == "config-add":
        # An `embedded_extras` expansion is an island where its parent sits (v1
        # §6); only a positionless addition trails.
        inherited = upstream_order.get(name)
        if inherited is not None:
            return (_UPSTREAM, inherited, "")
        # No upstream order to inherit, so alphabetical, as a trailing block.
        return (_CONDA_FORGE_ONLY, 0, name)

    position = upstream_order.get(name)
    if position is None:
        # Upstream-derived but not in the index: sorts to the end of the
        # upstream block.
        return (_UPSTREAM, len(upstream_order), name)
    return (_UPSTREAM, position, "")
