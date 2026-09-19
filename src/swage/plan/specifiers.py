"""Specifier arithmetic the grid needs: parse, intersect, test, render.

Nothing here knows about markers, builds or artifacts. It is what
DESIGN.md §9.3 step 7 does to the declarations an artifact asks for once the
grid has decided which those are, and what §9.6 says about the order clauses
are written in.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from packaging.specifiers import InvalidSpecifier, Specifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from swage.upstream import UpstreamRequirement

from .errors import PlanError

__all__ = [
    "declared_order",
    "expand_compatible",
    "parse_specifier",
    "render_specifier",
    "satisfiable",
]


def parse_specifier(variant: UpstreamRequirement, name: str) -> SpecifierSet:
    try:
        return SpecifierSet(variant.specifier)
    except InvalidSpecifier as exc:
        raise PlanError(
            f"{name}: cannot parse constraint {variant.specifier!r}: {exc}"
        ) from exc


def declared_order(variants: Sequence[UpstreamRequirement]) -> dict[str, int]:
    """Where each clause first appeared in what upstream wrote.

    `UpstreamRequirement.specifier` has been through packaging, which sorts
    clauses alphabetically, so the declared order survives only in ``raw``.
    It orders several exclusions among themselves (DESIGN.md §9.6); the
    bounds are placed by `render_specifier`'s canonical order.
    """
    position: dict[str, int] = {}
    for variant in variants:
        constraint = variant.raw.partition(";")[0]
        for clause in constraint.split(","):
            text = re.sub(r"^[^<>=!~]*", "", clause.strip()).strip()
            if text:
                position.setdefault(text, len(position))
    return position


def render_specifier(specifier: SpecifierSet, declared: Mapping[str, int]) -> str:
    """Reduce the intersection to its tightest clauses, in recipe order.

    Floor, then ceiling, then exclusions in the order upstream declared them
    (DESIGN.md §9.6; the argument is v1 §6). A ``~=`` is spelled out as the
    bounds it means first. A set containing ``==`` or ``===`` is left in
    declared order rather than reduced: untidy is recoverable, wrong is not.
    """
    clauses = expand_compatible(specifier)
    if not clauses:
        return ""

    def declared_position(text: str) -> int:
        return declared.get(text, len(declared))

    if any(clause.operator in {"==", "==="} for clause in clauses):
        return ",".join(sorted((str(c) for c in clauses), key=declared_position))

    lower = max(
        (c for c in clauses if c.operator in {">=", ">"}),
        key=lambda c: (Version(c.version), c.operator == ">"),
        default=None,
    )
    upper = min(
        (c for c in clauses if c.operator in {"<=", "<"}),
        key=lambda c: (Version(c.version), c.operator == "<="),
        default=None,
    )
    bounds = [str(c) for c in (lower, upper) if c is not None]
    exclusions = sorted(
        {str(c) for c in clauses if c.operator == "!="}, key=declared_position
    )
    return ",".join(bounds + exclusions)


def expand_compatible(specifier: SpecifierSet) -> list[Specifier]:
    """The set's clauses, with each ``~=`` as the ``>=`` and ``<`` it means.

    PEP 440: ``~=V`` is ``>=V, ==P.*`` with ``P`` the release segment less
    its last component, so ``~=3.2`` is ``>=3.2,<4`` and ``~=3.2.1`` is
    ``>=3.2.1,<3.3``. The floor keeps upstream's spelling of the version.
    """
    expanded: list[Specifier] = []
    for clause in specifier:
        if clause.operator != "~=":
            expanded.append(clause)
            continue
        prefix = Version(clause.version).release[:-1]
        ceiling = ".".join(str(part) for part in (*prefix[:-1], prefix[-1] + 1))
        expanded.append(Specifier(f">={clause.version}"))
        expanded.append(Specifier(f"<{ceiling}"))
    return expanded


def satisfiable(specifier: SpecifierSet) -> bool:
    """Whether any version at all satisfies the whole set.

    Decided by trying the versions the set mentions, a point just above each,
    and the two extremes: a range is non-empty exactly when one of its own
    boundaries, or a point just past one, falls inside it. Losing a candidate
    could only lose a witness, never invent one, so the worst case is a stop
    rather than a bad merge.
    """
    candidates = {Version("0"), Version("99999")}
    for clause in specifier:
        try:
            version = Version(clause.version.rstrip(".*"))
        except InvalidVersion:
            continue
        candidates.add(version)
        above = _just_above(version)
        if above is not None:
            candidates.add(above)
    return any(
        specifier.contains(candidate, prereleases=True) for candidate in candidates
    )


def _just_above(version: Version) -> Version | None:
    """The smallest version this one's release segment can be nudged to.

    ``0.20b0.1`` is not a version and ``.post1`` is excluded by ``>V``, so the
    release segment is bumped instead: ``0.20b0`` becomes ``0.20.1``. The
    epoch is carried, or the comparison would be against another series.
    """
    release = ".".join(str(part) for part in version.release)
    epoch = f"{version.epoch}!" if version.epoch else ""
    try:
        candidate = Version(f"{epoch}{release}.1")
    except InvalidVersion:  # pragma: no cover -- release parts are always ints
        return None
    return candidate if candidate > version else None
