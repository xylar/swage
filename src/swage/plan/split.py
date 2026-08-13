"""Write a package's upstream declarations as conditions on what is built.

An architecture-specific output is built **once per python and once per
platform**, so upstream's environment markers are not something to collapse --
they are something the recipe can carry (DESIGN.md 3.3.1.1, 3.3.4)::

    grpcio>=1.33.1,<1.66.0; python_version <"3.13"
    grpcio>=1.67.0       ; python_version >="3.13"

becomes::

    - if: python < "3.13"
      then: grpcio >=1.33.1,<1.66.0
      else: grpcio >=1.67.0

`reconcile` and this module are the same fidelity rule under different
constraints. A noarch output has one requirements list describing every python
it will be installed on, so the strictest bound wins and a comment records that
a choice was made. An arch output can say what upstream says, so it does -- and
must, or the package claims a constraint upstream never made and a solve can
fail for no reason. Nothing is chosen here, so nothing is commented.

and a platform marker becomes ``if: win`` on the axis the build already varies
over.

**The axes are sampled, not solved.** Every marker is evaluated for every
python minor release on every platform, and the resulting grid is what decides
the answer: releases that agree merge into a run, platforms that agree into a
group, and each becomes one condition. That is exact at the granularity a
recipe can express, which is the granularity conda-forge builds at -- one
artifact per minor release per platform, and no way to write a condition finer.
A marker distinguishing two patch releases of one minor is therefore
inexpressible, and inexpressible is a stop rather than a guess.

**A marker mixing the two axes is a stop as well**, and the grid is what
notices: where the answer varies by python *and* by platform, writing it needs
conditions nested one inside the other, which is not a structure to invent
before a feedstock asks for it.

**The build floor does not apply here.** `python_min` is the bottom of the
range one noarch artifact has to serve; an arch output has no such range. A
variant that is unreachable on the pythons this feedstock actually builds
produces a condition that is simply never selected, and which pythons those are
is `.ci_support`'s answer rather than `python_min`'s.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from packaging.markers import Marker
from packaging.specifiers import SpecifierSet

from swage.upstream import UpstreamRequirement

from .errors import PlanError
from .markers import PLATFORM_AXIS, PYTHON_AXIS, marker_variables
from .reconcile import (
    declared_order,
    parse_marker,
    parse_specifier,
    render_specifier,
    satisfiable,
)

__all__ = ["Branch", "Split", "split_by_environment"]

#: conda-forge builds python 3. A marker gated on python 4 evaluates false
#: everywhere below, which is the right answer for an artifact nobody is
#: building, and a `python_version < "4"` bound is true everywhere, which is
#: the right answer for the bound almost every project writes.
_MAJOR = 3

#: How far up the axis to sample. Well past anything conda-forge will ship
#: before this code is rewritten, and the same horizon `markers` uses.
_CEILING = 40


@dataclass(frozen=True)
class Branch:
    """One run of python releases, and what upstream asks for across it."""

    #: The recipe condition, e.g. ``python < "3.13"``. None where the run
    #: covers the whole axis and the requirement is unconditional.
    condition: str | None
    #: The intersected specifier, e.g. ``">=1.67.0"``. Empty where upstream
    #: names the package without constraining it.
    specifier: str


@dataclass(frozen=True)
class Split:
    """How one dependency is written across the pythons an output is built for."""

    branches: tuple[Branch, ...]
    #: Whether the two branches partition the axis between them, in which case
    #: they are one `if:`/`then:`/`else:` entry rather than two `if:` entries.
    #: Three or more runs cannot be written that way and stay several entries.
    complementary: bool
    #: The variants that apply on some python. Empty where upstream asks for
    #: this package on none of them, which is a removal for the planner to
    #: decide rather than something to render.
    considered: tuple[UpstreamRequirement, ...]


def split_by_environment(
    name: str,
    variants: Sequence[UpstreamRequirement],
    constraint: str | None = None,
) -> Split:
    """Write every declaration of ``name`` as conditions on what is built.

    ``constraint`` is a bound config adds beyond what upstream declares
    (DESIGN.md 3.3.14). It holds on every build, so it is intersected into
    each cell of the grid rather than pasted onto one branch of the result.

    The grid is what makes the two axes one rule. A marker mixing them --
    `sys_platform == "win32" and python_version < "3.13"` -- makes the answer
    vary along both, and *that* is what stops the feedstock, rather than the
    shape of any one marker. Writing such a case correctly means conditions
    nested two deep or repeated per cell, and neither is something to invent
    before a real feedstock asks for it.
    """
    if not variants:
        raise PlanError(f"no upstream declarations of {name!r} to split")

    markers = [(variant, parse_marker(variant, name)) for variant in variants]
    for variant, marker in markers:
        if marker is not None:
            _refuse_other_axes(name, variant, marker)

    grid = {
        (minor, platform): _in_cell(name, markers, minor, platform, constraint)
        for minor in range(_CEILING)
        for platform in _PLATFORMS
    }
    considered = tuple(
        variant
        for variant, _ in markers
        if any(variant in active for active, _ in grid.values())
    )
    if not considered:
        return Split(branches=(), complementary=False, considered=())

    answers = {key: specifier for key, (_, specifier) in grid.items()}
    varies_by_python = any(
        answers[(minor, platform)] != answers[(0, platform)]
        for minor in range(_CEILING)
        for platform in _PLATFORMS
    )
    varies_by_platform = any(
        answers[(minor, platform)] != answers[(minor, _PLATFORMS[0])]
        for minor in range(_CEILING)
        for platform in _PLATFORMS
    )
    if varies_by_python and varies_by_platform:
        raise PlanError(_two_axes(name, markers))
    if varies_by_platform:
        return _over_platforms(answers, considered)
    return _over_pythons(answers, considered)


def _over_pythons(
    answers: Mapping[tuple[int, str], str | None],
    considered: tuple[UpstreamRequirement, ...],
) -> Split:
    """One branch per run of consecutive python releases that agree."""
    runs = _runs([answers[(minor, _PLATFORMS[0])] for minor in range(_CEILING)])
    branches = tuple(
        Branch(_python_condition(start, end), specifier)
        for start, end, specifier in runs
        if specifier is not None
    )
    return Split(
        branches=branches,
        complementary=len(branches) == 2 and len(runs) == 2,
        considered=considered,
    )


def _over_platforms(
    answers: Mapping[tuple[int, str], str | None],
    considered: tuple[UpstreamRequirement, ...],
) -> Split:
    """One branch per group of platforms that agree.

    The platform axis is three values rather than a line, so there is no run to
    merge -- the platforms giving the same answer are grouped, and the group is
    named the way a recipe names it: `unix` for the two that are not Windows,
    `not linux` for the two that are not Linux, and each platform by itself
    otherwise (DESIGN.md 3.3.4).
    """
    groups: dict[str | None, list[str]] = {}
    for platform in _PLATFORMS:
        groups.setdefault(answers[(0, platform)], []).append(platform)
    branches = tuple(
        Branch(_platform_condition(tuple(platforms)), specifier)
        for specifier, platforms in groups.items()
        if specifier is not None
    )
    return Split(
        branches=branches,
        complementary=len(branches) == 2 and len(groups) == 2,
        considered=considered,
    )


def _in_cell(
    name: str,
    markers: Sequence[tuple[UpstreamRequirement, Marker | None]],
    minor: int,
    platform: str,
    constraint: str | None,
) -> tuple[tuple[UpstreamRequirement, ...], str | None]:
    """Which variants apply to one build, and what they add up to there.

    The specifier is None where upstream does not ask for the package on this
    build at all -- which is not the same as asking for it without a
    constraint, and conflating the two would put a bare dependency line into a
    recipe that should not carry one.
    """
    active = _active(name, markers, minor, platform)
    if not active:
        return (), None

    combined = SpecifierSet()
    for variant in active:
        combined &= parse_specifier(variant, name)
    if constraint is not None:
        combined &= SpecifierSet(constraint)
    if not satisfiable(combined):
        raise PlanError(_contradiction(name, active, minor, constraint))
    return active, render_specifier(combined, declared_order(active))


def _active(
    name: str,
    markers: Sequence[tuple[UpstreamRequirement, Marker | None]],
    minor: int,
    platform: str,
) -> tuple[UpstreamRequirement, ...]:
    """The variants whose marker holds for one build.

    Both ends of the release are tried, and a marker that tells them apart is
    refused rather than resolved either way: `python_full_version >= "3.12.4"`
    is a real distinction that a recipe built once per minor release has no way
    to write down.
    """
    active: list[UpstreamRequirement] = []
    for variant, marker in markers:
        if marker is None:
            active.append(variant)
            continue
        holds = {
            marker.evaluate(_environment(minor, patch, platform)) for patch in (0, 99)
        }
        if len(holds) > 1:
            raise PlanError(
                f"cannot express upstream's marker for {name!r} as a build "
                "condition\n"
                f"    {_declaration(variant, name)} ; {variant.marker}\n"
                f"  it holds for some patch releases of python {_MAJOR}.{minor} "
                "and not others, and conda-forge builds one package per minor "
                "release rather than one per patch\n"
                "  resolve by hand"
            )
        if holds == {True}:
            active.append(variant)
    return tuple(active)


#: The platforms conda-forge builds for, as a marker sees them. Every variable
#: a marker may turn on is given a value, because `packaging` fills an unset
#: one from the interpreter running swage -- which would make a plan depend on
#: the machine it was made on.
_PLATFORMS = ("linux", "osx", "win")
_AS_MARKER = {
    "linux": {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix"},
    "osx": {"sys_platform": "darwin", "platform_system": "Darwin", "os_name": "posix"},
    "win": {"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt"},
}


def _environment(minor: int, patch: int, platform: str) -> dict[str, str]:
    return {
        "python_version": f"{_MAJOR}.{minor}",
        "python_full_version": f"{_MAJOR}.{minor}.{patch}",
        **_AS_MARKER[platform],
    }


def _runs(per_release: Sequence[str | None]) -> list[tuple[int, int, str | None]]:
    """Consecutive releases with the same answer, as ``(start, end, answer)``.

    ``end`` is inclusive, so a run is what one condition covers.
    """
    runs: list[tuple[int, int, str | None]] = []
    for minor, specifier in enumerate(per_release):
        if runs and runs[-1][2] == specifier:
            start, _, found = runs[-1]
            runs[-1] = (start, minor, found)
        else:
            runs.append((minor, minor, specifier))
    return runs


def _python_condition(start: int, end: int) -> str | None:
    """The condition selecting the releases from ``start`` to ``end``.

    Written the way the fleet writes it -- `apache-beam` hand-writes
    `if: python < "3.13"` -- and open-ended wherever the run is, so a
    dependency upstream gates at one version reads as one comparison rather
    than as a window with a bound nobody wrote.
    """
    below = f'python < "{_MAJOR}.{end + 1}"'
    above = f'python >= "{_MAJOR}.{start}"'
    if start == 0:
        return None if end == _CEILING - 1 else below
    return above if end == _CEILING - 1 else f"{above} and {below}"


def _platform_condition(platforms: tuple[str, ...]) -> str | None:
    """How a recipe names this group of platforms."""
    if len(platforms) == len(_PLATFORMS):
        return None
    if platforms == ("linux", "osx"):
        return "unix"
    if len(platforms) == 1:
        return platforms[0]
    excluded = next(one for one in _PLATFORMS if one not in platforms)
    return f"not {excluded}"


def _two_axes(
    name: str, markers: Sequence[tuple[UpstreamRequirement, Marker | None]]
) -> str:
    """What upstream said, when it varies by python *and* by platform."""
    quoted = "\n".join(
        f"    {_declaration(variant, name)} ; {variant.marker}"
        for variant, marker in markers
        if marker is not None
    )
    return (
        f"cannot yet write upstream's markers for {name!r} as build conditions\n"
        f"{quoted}\n"
        "  what upstream asks for varies by python version and by platform "
        "together, which needs conditions nested one inside the other\n"
        "  resolve by hand"
    )


def _refuse_other_axes(name: str, variant: UpstreamRequirement, marker: Marker) -> None:
    """Stop on a marker turning on something the build does not vary over.

    An arch output varies over python and over platform, and those are the
    axes a condition can key on. `platform_machine` and
    `platform_python_implementation` are neither, and a python condition that
    quietly ignored half of what upstream said would be worse than saying so.
    """
    other = sorted(marker_variables(marker) - PYTHON_AXIS - PLATFORM_AXIS)
    if not other:
        return
    raise PlanError(
        f"cannot write upstream's marker for {name!r} as a build condition\n"
        f"    {_declaration(variant, name)} ; {variant.marker}\n"
        f"  it turns on {', '.join(other)}, which is not something this "
        "output is built once for each of\n"
        "  resolve by hand"
    )


def _contradiction(
    name: str,
    active: Sequence[UpstreamRequirement],
    minor: int,
    constraint: str | None,
) -> str:
    """Two declarations that hold on the same python and cannot both be met.

    Unlike the noarch case (DESIGN.md 3.3.2) this is not an artefact of one
    package having to serve a range: these declarations apply to the *same*
    build, so no recipe of any shape could satisfy them and the metadata itself
    is what has to change.
    """
    width = max(len(_declaration(variant, name)) for variant in active)
    quoted = "\n".join(
        f"    {_declaration(variant, name):<{width}}"
        + (f" ; {variant.marker}" if variant.marker else "")
        for variant in active
    )
    added = ""
    if constraint is not None:
        added = f"\n  config also constrains it to {constraint}"
    return (
        f"contradictory upstream constraints for {name!r}\n"
        f"{quoted}\n"
        f"  all of these hold on python {_MAJOR}.{minor}, and no version "
        f"satisfies them together{added}\n"
        "  resolve by hand"
    )


def _declaration(variant: UpstreamRequirement, name: str) -> str:
    return f"{name}{variant.specifier}"
