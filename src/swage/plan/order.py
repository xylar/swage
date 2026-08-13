"""Order a requirements section the way swage writes it (DESIGN.md 6).

Consistency is a stated goal, so the ordering is a specified, tested component
rather than whatever a template happened to emit. Three rules, and the first is
the one that rules out the obvious implementation:

1. **Requirements from upstream appear in upstream's own source order**, not
   alphabetically. This keeps swage's diffs against upstream small and legible:
   a dependency added upstream lands where upstream put it, so the recipe diff
   and the upstream diff line up. A dependency upstream states per python range
   is one entry at one position, not several scattered by condition.
2. **`python`, then `pip`, come first** where they apply.
3. **Requirements conda-forge needs that upstream does not declare form a
   separate trailing block, alphabetized**, since they have no upstream order
   to inherit.

Rule 2 is the fleet's own convention rather than an imposition: across the
readable recipes, 159 `run` sections put `python` before a `pin_subpackage`
line and 2 put it after.

Sorting is stable, so anything the rules do not distinguish keeps the order it
arrived in. That matters for structural lines other than `python` and `pip` --
swage has no basis for reordering them and should not invent one.
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
    declaration order -- `AttributionIndex.order`, built by the same traversal
    that attributed the lines, so the two cannot disagree about what upstream
    said.
    """
    return tuple(sorted(entries, key=lambda entry: _key(entry, upstream_order)))


def _key(
    entry: PlannedEntry, upstream_order: Mapping[str, int]
) -> tuple[int, int, str]:
    name = entry.name

    if isinstance(entry, PlannedConditional) and entry.preserved:
        # A conditional swage did not author is structure it does not
        # understand: `mpi != "nompi"`, `is_abi3`, a cross-compilation block.
        # It keeps the order it arrived in, which is the recipe's own -- the
        # sort is stable, so equal keys are left alone. Sorting these by the
        # first name inside them shuffled seven entries in `e3sm-unified`'s
        # `run` and five in `magics`' `host`, which is swage rearranging a
        # recipe it was asked to reconcile.
        return (_OTHER_STRUCTURE, 0, "")

    if entry.provenance.origin == "recipe-kept":
        if entry.provenance.detail == KEPT_UNEXPLAINED:
            # Not structure, whatever the origin says. `recipe-kept` is an
            # allowlist and never a fallback (DESIGN.md 3.3.6), so a line swage
            # kept without being able to explain it carries that origin as a
            # placeholder -- and sorting it as structure hoisted it above every
            # upstream line in the section. It belongs in the trailing block
            # with the conda-forge-only additions it joins the moment somebody
            # writes it into `add_requirements`, so that documenting a line
            # does not also move it.
            return (_CONDA_FORGE_ONLY, 0, name)
        if name in _FIRST:
            return (_PYTHON_AND_PIP, _FIRST.index(name), "")
        # Everything else structural keeps its incoming order: a stable sort
        # leaves equal keys alone, and swage has no basis for choosing between
        # a `pin_subpackage` and a `compiler` line.
        return (_OTHER_STRUCTURE, 0, "")

    if entry.provenance.origin == "config-add":
        # An `embedded_extras` expansion is config-supplied but *does* have an
        # order to inherit: it is an island sitting where the requirement whose
        # extra it stands in for sits (DESIGN.md 6). Only a genuinely
        # positionless addition trails.
        inherited = upstream_order.get(name)
        if inherited is not None:
            return (_UPSTREAM, inherited, "")
        # No upstream order to inherit, so alphabetical is the only stable
        # answer -- and a trailing block makes the conda-forge-only additions
        # visible as a group rather than scattered through the section.
        return (_CONDA_FORGE_ONLY, 0, name)

    position = upstream_order.get(name)
    if position is None:
        # Upstream-derived but not in the index: it cannot be placed by
        # upstream's order, so it sorts to the end of the upstream block
        # rather than to an arbitrary position among it.
        return (_UPSTREAM, len(upstream_order), name)
    return (_UPSTREAM, position, "")
