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

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from packaging.markers import InvalidMarker, Marker
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from swage.upstream import UpstreamRequirement

from .errors import PlanError
from .markers import (
    PLATFORM_AXIS,
    PYTHON_AXIS,
    marker_variables,
    reachable_in_range,
    resolve_implementation,
    summarize_python,
)
from .python_min import PythonMin

__all__ = [
    "Reconciled",
    "declared_order",
    "parse_marker",
    "parse_specifier",
    "reconcile",
    "render_specifier",
    "satisfiable",
]

#: Operators that put a floor under a version, so the highest of them is what
#: decides which variant is binding.
_LOWER_BOUND_OPERATORS = frozenset({">=", ">", "==", "~="})
_UPPER_BOUND_OPERATORS = frozenset({"<=", "<"})


@dataclass(frozen=True)
class Reconciled:
    """One recipe line's worth of constraint, and why it says that."""

    #: The intersected specifier, e.g. ``">=2.3.3"``. Empty where upstream
    #: does not constrain the package at all, and empty too where every
    #: declaration of it was gated below `python_min` -- `considered` is what
    #: tells those two apart.
    specifier: str
    #: The comment to render above the line, e.g.
    #: ``"tightest of upstream's floors (python >=3.14)"``, or None where no
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
    python_max: Version | None = None,
    constraint: str | None = None,
    platform: str | None = None,
) -> Reconciled:
    """Reduce every declaration of ``name`` to a single constraint.

    ``python_max`` is the cap the recipe puts on its own `python` line, where
    it has one (DESIGN.md 3.3.3). It bounds the range markers are evaluated
    over from above exactly as ``python_min`` bounds it from below.

    ``constraint`` is a bound config records this feedstock as stating
    beyond what upstream declares -- from `constraints` or from
    `temporary_constraints`, which render identically and differ only in
    whether the feedstock is held for review (DESIGN.md 3.3.14). It is
    intersected in here rather than pasted on afterwards, so it goes through
    the same clause ordering and the same satisfiability check as everything
    upstream said.

    ``platform`` names the platform this artifact is built for, under the
    build model where conda-smithy renders one `noarch: python` package per
    platform (DESIGN.md 3.3.1.1). Everything else is unchanged: that artifact
    is still installed on every Python from the floor up, so the collapse is
    the same collapse. What changes is that a marker naming the platform is
    now answerable rather than a refusal, because the caller is asking once
    for each of them.
    """
    if not variants:
        raise PlanError(f"no upstream declarations of {name!r} to reconcile")

    reachable: list[UpstreamRequirement] = []
    for variant in variants:
        marker = parse_marker(variant, name)
        if marker is None:
            reachable.append(variant)
            continue
        _refuse_unvarying_axis(name, variant, marker, platform)
        if reachable_in_range(marker, python_min.version, python_max, platform):
            reachable.append(variant)

    if not reachable:
        # Every declaration is gated outside the range this package is
        # installed across, so upstream does not ask for it on any Python
        # conda-forge ships this recipe for. Dropping it is a removal decision
        # the planner makes, not one to make here.
        return Reconciled(specifier="", note=None, considered=())

    combined = SpecifierSet()
    for variant in reachable:
        combined &= parse_specifier(variant, name)

    if not satisfiable(combined):
        raise PlanError(_contradiction(name, reachable, python_min, feedstock))

    if constraint is not None:
        with_config = combined & SpecifierSet(constraint)
        if not satisfiable(with_config):
            # Reported apart from the upstream contradiction above, because
            # the fix is in a different file and quoting upstream's
            # declarations here would send the reader to the wrong one.
            raise PlanError(
                f"config constrains {name!r} to {constraint}, and no version "
                f"satisfying upstream's {combined} can meet it -- correct or "
                f"drop the config entry for {name!r}"
            )
        combined = with_config

    return Reconciled(
        specifier=render_specifier(combined, declared_order(reachable)),
        note=_note(reachable, platform),
        considered=tuple(reachable),
    )


def declared_order(variants: Sequence[UpstreamRequirement]) -> dict[str, int]:
    """Where each clause first appeared in what upstream actually wrote.

    `UpstreamRequirement.specifier` has already been through packaging, which
    sorts clauses alphabetically, so the declared order survives only in
    ``raw``. It is worth recovering: it is what orders several exclusions
    among themselves, where upstream's own sequence is the only thing to go
    on. The bounds are placed by `_render`'s canonical order rather than by
    this, so a project writing ``!=36.0.0`` before ``!=35.0.1`` keeps that
    pairing without dragging the ceiling along with it.
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
    """Reduce the intersection to its tightest clauses and order them for a recipe.

    Two things `SpecifierSet` will not do. It intersects by *unioning* clauses,
    so three declarations of `pandas` come out as ``>=2.1.2,>=2.2.3,>=2.3.3``
    when only the last binds; and `str()` orders clauses alphabetically.

    Ordering is **the bounds first -- floor, then ceiling -- and exclusions
    last**, each group in the order upstream declared it. A range reads as a
    range that way: ``>=2.14.1,<3.0.0,!=2.24.0,!=2.25.0`` says "2.14.1 up to
    3.0.0, minus two" rather than burying the ceiling behind the holes.

    This deliberately does not preserve the declared order of the bounds
    relative to the exclusions, and it cannot: the two metadata sources
    disagree about it. A `pyproject.toml` keeps its author's order, so
    `kubernetes` arrives as ``>=35.0.0,!=36.0.0,<37.0.0``; a `METADATA` source
    has been alphabetized by the build backend, so the same constraint arrives
    as ``!=36.0.0,<37.0.0,>=35.0.0``. Preserving either one makes a recipe's
    formatting depend on which file a sdist happened to ship, which is the
    problem DESIGN.md 3.6.1 solves for extra names and this solves for
    clauses. One canonical order is what makes goal 2 -- consistent formatting
    across feedstocks -- mean anything.

    The prior art split on this, which is why the corpus does too. The
    google-cloud tool canonicalized to exactly this order; the airflow tool
    passed the constraint through as upstream wrote it. Only one of them can
    be reproduced byte for byte, so the airflow family's single multi-clause
    recipe reformats once, and `KNOWN_DIFFERENCES` records that.

    Only the bound operators are reduced. An ``==``, ``~=`` or ``===`` anywhere
    in the set means the clauses are left exactly as they came, because
    simplifying those correctly is version-range algebra and getting it subtly
    wrong would change what the recipe demands. Untidy is recoverable; wrong is
    not.
    """
    clauses = list(specifier)
    if not clauses:
        return ""

    def declared_position(text: str) -> int:
        return declared.get(text, len(declared))

    if any(clause.operator in {"==", "~=", "==="} for clause in clauses):
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


def parse_marker(variant: UpstreamRequirement, name: str) -> Marker | None:
    """What the declaration is conditional on, or None if it is unconditional.

    The Python implementation is resolved to CPython on the way through, so
    everything downstream sees only axes conda-forge really builds along. A
    declaration whose whole marker was about the implementation comes back
    unconditional, which is what it is once PyPy is off the table.
    """
    if variant.marker is None:
        return None
    try:
        marker = Marker(variant.marker)
    except InvalidMarker as exc:
        raise PlanError(
            f"{name}: cannot parse marker {variant.marker!r}: {exc}"
        ) from exc
    return resolve_implementation(marker)


def parse_specifier(variant: UpstreamRequirement, name: str) -> SpecifierSet:
    try:
        return SpecifierSet(variant.specifier)
    except InvalidSpecifier as exc:
        raise PlanError(
            f"{name}: cannot parse constraint {variant.specifier!r}: {exc}"
        ) from exc


def _refuse_unvarying_axis(
    name: str,
    variant: UpstreamRequirement,
    marker: Marker,
    platform: str | None = None,
) -> None:
    """Stop on a marker naming something this artifact does not vary over.

    The message names *both* resolutions on purpose. "swage cannot do this" and
    "swage will not choose this for you" send the reader somewhere very
    different, and only the second is true here (DESIGN.md 3.3.4).

    With ``platform`` bound the package is built once per platform, so the
    platform axis is answerable and only the machine -- and anything swage does
    not model -- is left. `noarch_platforms` lists whole subdirs like
    `linux_64`, so a machine still does not vary within one artifact, but the
    reason is a different one and the message says so rather than talking
    about Pythons.
    """
    allowed = PYTHON_AXIS | PLATFORM_AXIS if platform is not None else PYTHON_AXIS
    other = sorted(marker_variables(marker) - allowed)
    if not other:
        return
    declaration = variant.raw or f"{name} {variant.specifier}; {variant.marker}"
    if platform is not None:
        raise PlanError(
            f"build-conditional constraint for {name!r}\n"
            f"    {declaration}\n"
            f"  the marker turns on {', '.join(other)}, which does not vary "
            "across one of the per-platform noarch packages this feedstock "
            "builds\n"
            "  resolve by hand"
        )
    raise PlanError(
        f"platform-conditional constraint for {name!r}\n"
        f"    {declaration}\n"
        f"  the marker turns on {', '.join(other)}, which does not vary across "
        "the Pythons one noarch package is installed on\n"
        "  two resolutions exist and both are packaging decisions, so swage "
        "picks neither:\n"
        "    - set noarch_platforms in conda-forge.yml and condition the "
        "dependency -- but swage does not edit conda-forge.yml, and the "
        "per-platform build strings that needs would change more of the "
        "recipe than swage is allowed to\n"
        "    - depend on it unconditionally, shipping a package inert "
        "elsewhere -- usually the right call, and still a judgment about what "
        "the package promises\n"
        "  resolve by hand"
    )


def satisfiable(specifier: SpecifierSet) -> bool:
    """Whether any version at all satisfies the whole set.

    Decided by trying the versions the set itself mentions, plus a point just
    above each and the two extremes: a range is non-empty exactly when one of
    its own boundaries, or a point just past one, falls inside it. Cheaper and
    harder to get wrong than reasoning about the operators analytically.

    Losing a candidate would only ever lose a witness, never invent one, so the
    worst case is calling a satisfiable range contradictory -- a stop rather
    than a bad merge, which is the direction to fail in.
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
    """The smallest version this one's *release* segment can be nudged to.

    Witnessing a strict range needs a version above the floor, and neither
    obvious spelling works. Suffixing the version text breaks outright --
    `0.20b0.1` is not a version, which is how
    `opentelemetry-instrumentation >=0.20b0` crashed the planner on a live
    google-cloud feedstock. Suffixing `.post1` parses but PEP 440 says `>V`
    excludes a post-release of V, so it is never inside the range it was built
    to witness. `.dev0` fails symmetrically against `<V`.

    Bumping the release segment sidesteps both: `0.20b0` becomes `0.20.1`,
    which is greater, is not a post-release, and is below anything a ceiling is
    realistically set to. The epoch is carried because dropping it would
    compare against a different series entirely.
    """
    release = ".".join(str(part) for part in version.release)
    epoch = f"{version.epoch}!" if version.epoch else ""
    try:
        candidate = Version(f"{epoch}{release}.1")
    except InvalidVersion:  # pragma: no cover -- release parts are always ints
        return None
    return candidate if candidate > version else None


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


def _note(
    reachable: Sequence[UpstreamRequirement], platform: str | None = None
) -> str | None:
    """Name the markers behind the bounds that ended up binding.

    Without this the recipe demands more than upstream does on most Pythons
    with nothing to say why, which reads as a mistake.

    **The wording is swage's own**, and deliberately neither tool's. The
    airflow tool wrote ``# more restrictive for python >=3.14`` and the
    google-cloud tool ``# more restrictive constraint for python >=3.14``, so
    the corpus carries both and DESIGN.md ended up quoting one in 3.3.1 and
    the other in 6. swage renders this comment rather than preserving it, so
    it has to settle on one spelling.

    Both inherited spellings say what the line *is* and neither says why it
    says that, which leaves the reader a wrong answer to reach for: "more
    restrictive for python >=3.14" reads as though the constraint applied
    only on 3.14 and up. It does not -- it binds on every Python conda-forge
    ships this package for, because there is one noarch artifact for all of
    them (DESIGN.md 3.3.1). Someone acting on that misreading would make the
    line conditional, which is the one edit this comment exists to prevent.

    ``tightest of upstream's floors (python >=3.14)`` states the selection
    instead: several variants were in play, the strictest won, and the
    parenthetical names which one it was.

    **Both ends are named where they came from different declarations.** A
    line can take its floor from one variant and its ceiling from another, and
    a note that mentions only the floor leaves the reader to assume the whole
    constraint came from there -- which is the same wrong answer in a quieter
    form. `google-ads` had written that distinction by hand before swage ever
    ran on it, and the maintainer asked for it to be kept. The two are only
    ever both named when they really do differ, so the common line keeps the
    short sentence.
    """
    floor = _binding(reachable, _floor, most=max)
    ceiling = _binding(reachable, _ceiling, most=min)
    floor_marker = _marker_of(floor, platform)
    ceiling_marker = _marker_of(ceiling, platform)

    if floor_marker is not None and ceiling_marker not in (None, floor_marker):
        return (
            f"tightest of upstream's floors ({floor_marker}) "
            f"and ceilings ({ceiling_marker})"
        )
    if floor_marker is not None:
        return f"tightest of upstream's floors ({floor_marker})"
    if ceiling_marker is not None:
        # The mirror image, and rarer: every declaration agrees on the floor
        # and one of them alone caps the version. The recipe still demands
        # more than upstream does on most Pythons.
        return f"tightest of upstream's ceilings ({ceiling_marker})"
    return None


def _binding(
    reachable: Sequence[UpstreamRequirement],
    bound: Callable[[UpstreamRequirement], Version | None],
    most: Callable[[Version, Version], Version],
) -> UpstreamRequirement | None:
    """The declaration whose bound survives the intersection.

    `most` is `max` for a floor and `min` for a ceiling, which is the whole of
    the difference between the two ends: intersecting keeps the highest floor
    and the lowest ceiling.
    """
    binding: UpstreamRequirement | None = None
    winning: Version | None = None
    for variant in reachable:
        version = bound(variant)
        if version is None:
            continue
        if winning is None or most(version, winning) != winning:
            winning, binding = version, variant
    return binding


def _marker_of(
    variant: UpstreamRequirement | None, platform: str | None = None
) -> str | None:
    """How this declaration's marker reads in a comment, if it has one.

    A marker that says nothing about the Python version says nothing this note
    exists to say. That cannot happen on the ordinary noarch path, where any
    other axis is a refusal -- but with a platform bound it is the common case,
    and the comment would repeat the condition the line is already under:
    `# tightest of upstream's floors (sys_platform == "darwin")` sitting inside
    `if: osx`.
    """
    if variant is None or variant.marker is None:
        return None
    marker = resolve_implementation(Marker(variant.marker))
    if marker is None:
        # Only the implementation was ever in question, and that is settled,
        # so the bound binds everywhere and there is nothing to attribute.
        return None
    if platform is not None and not marker_variables(marker) & PYTHON_AXIS:
        return None
    return summarize_python(marker)


def _floor(variant: UpstreamRequirement) -> Version | None:
    """The highest version this variant puts a floor at, if any."""
    return _bound(variant, _LOWER_BOUND_OPERATORS, max)


def _ceiling(variant: UpstreamRequirement) -> Version | None:
    """The lowest version this variant caps at, if any.

    `==` and `~=` are not counted, though they bound above as well as below:
    `render_specifier` leaves a set containing either exactly as upstream
    wrote it rather than reducing it, so there is no "surviving" ceiling to
    attribute.
    """
    return _bound(variant, _UPPER_BOUND_OPERATORS, min)


def _bound(
    variant: UpstreamRequirement,
    operators: frozenset[str],
    most: Callable[[list[Version]], Version],
) -> Version | None:
    versions: list[Version] = []
    for clause in SpecifierSet(variant.specifier):
        if clause.operator not in operators:
            continue
        try:
            versions.append(Version(clause.version.rstrip(".*")))
        except InvalidVersion:
            continue
    return most(versions) if versions else None
