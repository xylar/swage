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

**The build floor does not apply here, but the matrix does.** `python_min` is
the bottom of the range one noarch artifact has to serve, and an arch output
has no such range -- which pythons it is built for is `.ci_support`'s answer
rather than `python_min`'s. That answer is still needed, because a declaration
reaching none of those pythons describes an artifact conda-forge does not
produce: it is dropped before anything is refused or rendered.

`pyodps` is why. Upstream asks for `oldest-supported-numpy` on aarch64 below
python 3.9, the feedstock is built for 3.10 and up, and swage refused the whole
feedstock over the machine half of a marker whose python half had already made
it moot -- a maintainer sent to resolve by hand a case that cannot arise. A
never-selected condition would have been the milder version of the same
mistake, and it is not written either.
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
    pythons: Sequence[int] = (),
) -> Split:
    """Write every declaration of ``name`` as conditions on what is built.

    ``constraint`` is a bound config adds beyond what upstream declares
    (DESIGN.md 3.3.14). It holds on every build, so it is intersected into
    each cell of the grid rather than pasted onto one branch of the result.

    ``pythons`` is the minor releases this feedstock is built for, from
    `.ci_support`. Empty means the whole axis, which is what a caller with no
    rendered variants to read has to assume.

    The grid is what makes the two axes one rule. A marker mixing them --
    `sys_platform == "win32" and python_version < "3.13"` -- makes the answer
    vary along both, and *that* is what stops the feedstock, rather than the
    shape of any one marker. Writing such a case correctly means conditions
    nested two deep or repeated per cell, and neither is something to invent
    before a real feedstock asks for it.
    """
    if not variants:
        raise PlanError(f"no upstream declarations of {name!r} to split")

    minors = tuple(sorted(set(pythons))) or tuple(range(_CEILING))
    markers = [(variant, parse_marker(variant, name)) for variant in variants]
    # Before anything is refused or rendered, drop what reaches no build at
    # all. A declaration gated below the oldest python this feedstock is built
    # for describes an artifact that does not exist, so refusing the feedstock
    # over what it says asks a maintainer to resolve a case that cannot arise.
    markers = [
        (variant, marker) for variant, marker in markers if _reaches(marker, minors)
    ]
    if not markers:
        return Split(branches=(), complementary=False, considered=())

    for variant, marker in markers:
        if marker is not None:
            _refuse_other_axes(name, variant, marker)

    grid = {
        (minor, platform): _in_cell(name, markers, minor, platform, constraint)
        for minor in minors
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
        answers[(minor, platform)] != answers[(minors[0], platform)]
        for minor in minors
        for platform in _PLATFORMS
    )
    varies_by_platform = any(
        answers[(minor, platform)] != answers[(minor, _PLATFORMS[0])]
        for minor in minors
        for platform in _PLATFORMS
    )
    if varies_by_python and varies_by_platform:
        raise PlanError(_two_axes(name, markers))
    if varies_by_platform:
        return _over_platforms(answers, considered)
    return _over_pythons(answers, considered, minors)


def _reaches(marker: Marker | None, minors: Sequence[int]) -> bool:
    """Whether a declaration can hold on anything this feedstock builds.

    Sampled across every axis rather than solved, and across the machines
    conda-forge builds as well as the platforms -- so a marker naming a machine
    is answered here on its own terms instead of being taken as unreachable by
    a sampling that never mentioned one. What swage does with a *reachable*
    machine marker is a separate question, and `_refuse_other_axes` still
    answers it.
    """
    if marker is None:
        return True
    return any(
        marker.evaluate(_environment(minor, patch, platform, machine))
        for minor in minors
        for platform in _PLATFORMS
        for machine in _MACHINES[platform]
        for patch in (0, 99)
    )


def _over_pythons(
    answers: Mapping[tuple[int, str], str | None],
    considered: tuple[UpstreamRequirement, ...],
    minors: Sequence[int],
) -> Split:
    """One branch per run of consecutive python releases that agree."""
    runs = _runs([(minor, answers[(minor, _PLATFORMS[0])]) for minor in minors])
    branches = tuple(
        Branch(_python_condition(start, end, minors[0], minors[-1]), specifier)
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

#: The machines conda-forge builds each platform for, spelled as a marker sees
#: them -- `aarch64` on linux and `arm64` on macOS are the same silicon under
#: two names. Sampled when deciding whether a declaration reaches any build at
#: all; the recipe cannot yet key a condition on them, which is why a machine
#: marker that *does* reach one is still refused.
_MACHINES = {
    "linux": ("x86_64", "aarch64", "ppc64le", "s390x"),
    "osx": ("x86_64", "arm64"),
    "win": ("AMD64", "ARM64"),
}


def _environment(
    minor: int, patch: int, platform: str, machine: str | None = None
) -> dict[str, str]:
    return {
        "python_version": f"{_MAJOR}.{minor}",
        "python_full_version": f"{_MAJOR}.{minor}.{patch}",
        **_AS_MARKER[platform],
        "platform_machine": machine if machine is not None else _MACHINES[platform][0],
    }


def _runs(
    per_release: Sequence[tuple[int, str | None]],
) -> list[tuple[int, int, str | None]]:
    """Consecutive releases with the same answer, as ``(start, end, answer)``.

    ``end`` is inclusive, so a run is what one condition covers. Releases are
    named rather than counted, because the sampled axis starts at whatever the
    feedstock's oldest build is rather than at zero.
    """
    runs: list[tuple[int, int, str | None]] = []
    for minor, specifier in per_release:
        if runs and runs[-1][2] == specifier:
            start, _, found = runs[-1]
            runs[-1] = (start, minor, found)
        else:
            runs.append((minor, minor, specifier))
    return runs


def _python_condition(start: int, end: int, floor: int, ceiling: int) -> str | None:
    """The condition selecting the releases from ``start`` to ``end``.

    Written the way the fleet writes it -- `apache-beam` hand-writes
    `if: python < "3.13"` -- and open-ended wherever the run is, so a
    dependency upstream gates at one version reads as one comparison rather
    than as a window with a bound nobody wrote.

    ``floor`` and ``ceiling`` are the ends of the sampled axis: a run touching
    either is open-ended there, because there is no build beyond it to exclude.
    A run bounded by the oldest python a feedstock builds would otherwise
    render `python >= "3.10"` -- true of every artifact, and read by the next
    person as a constraint upstream asked for.
    """
    below = f'python < "{_MAJOR}.{end + 1}"'
    above = f'python >= "{_MAJOR}.{start}"'
    if start == floor:
        return None if end == ceiling else below
    return above if end == ceiling else f"{above} and {below}"


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
