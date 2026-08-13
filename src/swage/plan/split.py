"""Write a package's upstream declarations as conditions on the python built.

An architecture-specific output is built **once per python**, so upstream's
environment markers are not something to collapse -- they are something the
recipe can carry (DESIGN.md 3.3.1.1)::

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

**The axis is sampled, not solved.** Every python minor release is evaluated
against every marker, consecutive releases with the same answer are merged into
a run, and each run becomes one condition. That is exact at the granularity a
recipe can express, which is the same granularity conda-forge builds at: there
is one artifact per minor release and no way to write a condition finer than
one. A marker that distinguishes two patch releases of the same minor is
therefore inexpressible, and inexpressible is a stop rather than a guess.

**The build floor does not apply here.** `python_min` is the bottom of the
range one noarch artifact has to serve; an arch output has no such range. A
variant that is unreachable on the pythons this feedstock actually builds
produces a condition that is simply never selected, and which pythons those are
is `.ci_support`'s answer rather than `python_min`'s.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from packaging.markers import Marker
from packaging.specifiers import SpecifierSet

from swage.upstream import UpstreamRequirement

from .errors import PlanError
from .markers import PYTHON_AXIS, marker_variables
from .reconcile import (
    declared_order,
    parse_marker,
    parse_specifier,
    render_specifier,
    satisfiable,
)

__all__ = ["Branch", "Split", "split_by_python"]

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


def split_by_python(
    name: str,
    variants: Sequence[UpstreamRequirement],
    constraint: str | None = None,
) -> Split:
    """Write every declaration of ``name`` as conditions on the python built.

    ``constraint`` is a bound config adds beyond what upstream declares
    (DESIGN.md 3.3.14). It holds on every python, so it is intersected into
    each run rather than pasted onto one of them.
    """
    if not variants:
        raise PlanError(f"no upstream declarations of {name!r} to split")

    markers = [(variant, parse_marker(variant, name)) for variant in variants]
    for variant, marker in markers:
        if marker is not None:
            _refuse_other_axes(name, variant, marker)

    per_release = [
        _at_release(name, markers, minor, constraint) for minor in range(_CEILING)
    ]
    considered = tuple(
        variant
        for variant, _ in markers
        if any(variant in active for active, _ in per_release)
    )
    if not considered:
        return Split(branches=(), complementary=False, considered=())

    runs = _runs([specifier for _, specifier in per_release])
    branches = tuple(
        Branch(_condition(start, end), specifier)
        for start, end, specifier in runs
        if specifier is not None
    )
    return Split(
        branches=branches,
        complementary=len(branches) == 2 and len(runs) == 2,
        considered=considered,
    )


def _at_release(
    name: str,
    markers: Sequence[tuple[UpstreamRequirement, Marker | None]],
    minor: int,
    constraint: str | None,
) -> tuple[tuple[UpstreamRequirement, ...], str | None]:
    """Which variants apply on ``3.minor``, and what they add up to there.

    The specifier is None where upstream asks for the package on this release
    at all -- which is not the same as asking for it without a constraint, and
    conflating the two would put a bare dependency line into a recipe that
    should not carry one.
    """
    active = _active(name, markers, minor)
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
) -> tuple[UpstreamRequirement, ...]:
    """The variants whose marker holds on ``3.minor``.

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
        holds = {marker.evaluate(_environment(minor, patch)) for patch in (0, 99)}
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


def _environment(minor: int, patch: int) -> dict[str, str]:
    return {
        "python_version": f"{_MAJOR}.{minor}",
        "python_full_version": f"{_MAJOR}.{minor}.{patch}",
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


def _condition(start: int, end: int) -> str | None:
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


def _refuse_other_axes(name: str, variant: UpstreamRequirement, marker: Marker) -> None:
    """Stop on a marker this cannot put on the python axis.

    Temporary, and narrower than it looks: an arch output builds once per
    platform as well as once per python, so a `sys_platform` marker is a
    condition this output could carry too (DESIGN.md 3.3.4). Until that is
    written, saying so beats writing a python condition that ignores half of
    what upstream said.
    """
    other = sorted(marker_variables(marker) - PYTHON_AXIS)
    if not other:
        return
    raise PlanError(
        f"cannot yet write upstream's marker for {name!r} as a build "
        "condition\n"
        f"    {_declaration(variant, name)} ; {variant.marker}\n"
        f"  it turns on {', '.join(other)} rather than on the python version\n"
        "  update this feedstock by hand"
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
