"""Collapse a package's several upstream declarations into one recipe line.

A project routinely declares one dependency several times, differentiated by
environment markers::

    pandas>=2.1.2; python_version <"3.13"
    pandas>=2.2.3; python_version >="3.13" and python_version <"3.14"
    pandas>=2.3.3; python_version >="3.14"

conda-forge builds one `noarch: python` package installed on every Python from
`python_min` up, so the recipe gets a single `pandas` line and that line has to
hold for all of them (DESIGN.md 3.3.1):

1. discard variants whose marker cannot be true for any Python >= `python_min`
2. intersect the specifiers of everything that survives
3. an empty intersection is a stop, never a guess (DESIGN.md 3.3.2)
4. otherwise emit the intersection, with a comment where the binding bound came
   from a marker-qualified variant

Step 2 is deliberately stricter than upstream. On Python 3.10 upstream would
accept `pandas >=2.1.2` and the recipe demands `>=2.3.3`. One artifact cannot
do better, and the comment from step 4 is what stops that looking like a
mistake to the next reader.

> **The prior art gets this wrong, silently.** The airflow tool's
> `_merge_requirement_group` gathers only `>=` bounds, takes the highest, and
> skips any variant without one -- so given `<2.1.2` and `>=2.3.3` it emits
> `pandas >=2.3.3` and the upper bound vanishes with no warning. That is the
> class of failure the stop in step 3 exists to eliminate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from packaging.markers import InvalidMarker, Marker
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from swage.upstream import UpstreamRequirement

from .errors import PlanError
from .markers import PYTHON_AXIS, marker_variables, reachable_above, summarize_python
from .python_min import PythonMin

__all__ = ["Reconciled", "reconcile"]

#: Operators that put a floor under a version, so the highest of them is what
#: decides which variant is binding.
_LOWER_BOUND_OPERATORS = frozenset({">=", ">", "==", "~="})


@dataclass(frozen=True)
class Reconciled:
    """One recipe line's worth of constraint, and why it says that."""

    #: The intersected specifier, e.g. ``">=2.3.3"``. Empty where upstream
    #: does not constrain the package at all, and empty too where every
    #: declaration of it was gated below `python_min` -- `considered` is what
    #: tells those two apart.
    specifier: str
    #: The comment to render above the line, e.g.
    #: ``"more restrictive for python >=3.14"``, or None where no
    #: marker-qualified variant is doing the work.
    note: str | None
    #: The variants that were reachable and therefore intersected. Variants
    #: below `python_min` are not here -- they describe a Python this package
    #: will never be installed on.
    considered: tuple[UpstreamRequirement, ...]


def reconcile(
    name: str,
    variants: Sequence[UpstreamRequirement],
    python_min: PythonMin,
    feedstock: str | None = None,
) -> Reconciled:
    """Reduce every declaration of ``name`` to a single constraint."""
    if not variants:
        raise PlanError(f"no upstream declarations of {name!r} to reconcile")

    reachable: list[UpstreamRequirement] = []
    for variant in variants:
        marker = _marker(variant, name)
        if marker is None:
            reachable.append(variant)
            continue
        _refuse_non_python_axis(name, variant, marker)
        if reachable_above(marker, python_min.version):
            reachable.append(variant)

    if not reachable:
        # Every declaration is gated below the build floor, so upstream does
        # not ask for this package on any Python conda-forge ships. Dropping it
        # is a removal decision the planner makes, not one to make here.
        return Reconciled(specifier="", note=None, considered=())

    combined = SpecifierSet()
    for variant in reachable:
        combined &= _specifier(variant, name)

    if not _satisfiable(combined):
        raise PlanError(_contradiction(name, reachable, python_min, feedstock))

    return Reconciled(
        specifier=_render(combined),
        note=_note(reachable),
        considered=tuple(reachable),
    )


def _render(specifier: SpecifierSet) -> str:
    """Reduce the intersection to its tightest clauses and order them for a recipe.

    Two things `SpecifierSet` will not do. It intersects by *unioning* clauses,
    so three declarations of `pandas` come out as
    ``>=2.1.2,>=2.2.3,>=2.3.3`` when only the last binds; and `str()` orders
    clauses alphabetically, which puts the upper bound first. Recipes are
    written ``>=2.21.0,<3.0.0`` -- floor, then ceiling -- so that is what gets
    rendered.

    Only the bound operators are reduced. An ``==``, ``~=`` or ``===`` anywhere
    in the set means the clauses are left exactly as they came, because
    simplifying those correctly is version-range algebra and getting it subtly
    wrong would change what the recipe demands. Untidy is recoverable; wrong is
    not.
    """
    clauses = list(specifier)
    if not clauses:
        return ""
    if any(clause.operator in {"==", "~=", "==="} for clause in clauses):
        return ",".join(str(clause) for clause in clauses)

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
    excluded = sorted(
        {str(c) for c in clauses if c.operator == "!="},
        key=lambda text: Version(text[2:]),
    )
    ordered = [str(bound) for bound in (lower, upper) if bound is not None]
    return ",".join(ordered + excluded)


def _marker(variant: UpstreamRequirement, name: str) -> Marker | None:
    if variant.marker is None:
        return None
    try:
        return Marker(variant.marker)
    except InvalidMarker as exc:
        raise PlanError(
            f"{name}: cannot parse marker {variant.marker!r}: {exc}"
        ) from exc


def _specifier(variant: UpstreamRequirement, name: str) -> SpecifierSet:
    try:
        return SpecifierSet(variant.specifier)
    except InvalidSpecifier as exc:
        raise PlanError(
            f"{name}: cannot parse constraint {variant.specifier!r}: {exc}"
        ) from exc


def _refuse_non_python_axis(
    name: str, variant: UpstreamRequirement, marker: Marker
) -> None:
    """Stop on a marker swage cannot reduce to the Python-version axis.

    The message names *both* resolutions on purpose. "swage cannot do this" and
    "swage will not choose this for you" send the reader somewhere very
    different, and only the second is true here (DESIGN.md 3.3.4).
    """
    other = sorted(marker_variables(marker) - PYTHON_AXIS)
    if not other:
        return
    raise PlanError(
        f"platform-conditional constraint for {name!r}\n"
        f"    {variant.raw or f'{name} {variant.specifier}; {variant.marker}'}\n"
        f"  the marker turns on {', '.join(other)}, which does not vary across "
        "the Pythons one noarch package is installed on\n"
        "  two resolutions exist and both are packaging decisions, so swage "
        "picks neither:\n"
        "    - set noarch_platforms in conda-forge.yml and condition the "
        "dependency -- but conda-forge.yml is off limits (7), and the "
        "per-platform build strings it needs are a diff gate G5 forbids\n"
        "    - depend on it unconditionally, shipping a package inert "
        "elsewhere -- usually the right call, and still a judgement about what "
        "the package promises\n"
        "  resolve by hand"
    )


def _satisfiable(specifier: SpecifierSet) -> bool:
    """Whether any version at all satisfies the whole set.

    Decided by trying the versions the set itself mentions, plus a point either
    side of each: a range is non-empty exactly when one of its own boundaries,
    or a neighbour of one, falls inside it. Cheaper and harder to get wrong
    than reasoning about the operators analytically.
    """
    candidates = {Version("0"), Version("99999")}
    for clause in specifier:
        try:
            version = Version(clause.version.rstrip(".*"))
        except InvalidVersion:
            continue
        candidates.add(version)
        candidates.add(Version(f"{version}.1"))
        candidates.add(Version(f"{version}.dev0"))
    return any(
        specifier.contains(candidate, prereleases=True) for candidate in candidates
    )


def _contradiction(
    name: str,
    variants: Sequence[UpstreamRequirement],
    python_min: PythonMin,
    feedstock: str | None,
) -> str:
    """The message DESIGN.md 3.3.2 specifies, quoting the conflict.

    An error nobody can act on is barely better than the silent drop it
    replaces, so this has to be enough to resolve the feedstock without
    re-deriving anything.
    """
    width = max(len(_declaration(v, name)) for v in variants)
    quoted = "\n".join(
        f"    {_declaration(v, name):<{width}}" + (f" ; {v.marker}" if v.marker else "")
        for v in variants
    )
    target = (
        f"config/feedstocks/{feedstock}.yaml"
        if feedstock
        else "this feedstock's config file"
    )
    return (
        f"contradictory upstream constraints for {name!r}\n"
        f"{quoted}\n"
        f"  no single version satisfies them all across python "
        f">={python_min.value} (python_min, from {python_min.source}),\n"
        "  and conda-forge builds one noarch package for all of them\n"
        f"  resolve by hand, or pin the intended constraint in {target}"
    )


def _declaration(variant: UpstreamRequirement, name: str) -> str:
    return f"{name}{variant.specifier}"


def _note(reachable: Sequence[UpstreamRequirement]) -> str | None:
    """Name the marker behind the binding lower bound, where there is one.

    Without this the recipe demands more than upstream does on most Pythons
    with nothing to say why, which reads as a mistake.
    """
    binding: UpstreamRequirement | None = None
    highest: Version | None = None
    for variant in reachable:
        floor = _floor(variant)
        if floor is not None and (highest is None or floor > highest):
            highest, binding = floor, variant
    if binding is None or binding.marker is None:
        return None
    return f"more restrictive for {summarize_python(Marker(binding.marker))}"


def _floor(variant: UpstreamRequirement) -> Version | None:
    """The highest version this variant puts a floor at, if any."""
    floors: list[Version] = []
    for clause in SpecifierSet(variant.specifier):
        if clause.operator not in _LOWER_BOUND_OPERATORS:
            continue
        try:
            floors.append(Version(clause.version.rstrip(".*")))
        except InvalidVersion:
            continue
    return max(floors) if floors else None
