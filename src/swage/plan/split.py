"""Write a package's upstream declarations as conditions on what is built.

An architecture-specific output is built **once per python and once per
platform**, so upstream's environment markers are not something to collapse --
they are something the recipe can carry (DESIGN.md 3.3.1.1, 3.3.4)::

    grpcio>=1.33.1,<1.66.0; python_version <"3.13"
    grpcio>=1.67.0       ; python_version >="3.13"

becomes::

    - if: match(python, "<3.13")
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
from packaging.version import Version

from swage.upstream import UpstreamRequirement

from .errors import PlanError
from .markers import (
    MACHINE_AXIS,
    PLATFORM_AXIS,
    PLATFORM_MARKERS,
    PYTHON_AXIS,
    marker_variables,
    optimistic,
)
from .python_min import PythonMin
from .reconcile import (
    declared_order,
    parse_marker,
    parse_specifier,
    reconcile,
    render_specifier,
    satisfiable,
)

__all__ = ["Branch", "Split", "split_by_environment", "split_by_platform"]

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

    #: The recipe condition, e.g. ``match(python, "<3.13")``. None where the run
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
    targets: Sequence[str] = (),
) -> Split:
    """Write every declaration of ``name`` as conditions on what is built.

    ``constraint`` is a bound config adds beyond what upstream declares
    (DESIGN.md 3.3.14), from either constraints key. It holds on every
    build, so it is intersected into
    each cell of the grid rather than pasted onto one branch of the result.

    ``pythons`` is the minor releases this feedstock is built for, from
    `.ci_support`. Empty means the whole axis, which is what a caller with no
    rendered variants to read has to assume.

    ``targets`` is the subdirs conda-smithy rendered for this feedstock,
    `linux-64` and `osx-arm64` and the rest, and it is **only ever consulted
    to avoid a refusal**.

    The platform axis is a property of the feedstock rather than of the fleet
    (DESIGN.md 3.3.1), but acting on that everywhere would rewrite conditions
    that are already right. `mplcairo` builds no Windows, so upstream's
    unix-only `pycairo` covers every build it makes -- and answering
    "unconditional" there would delete an `if: unix` that says exactly what
    upstream declares, stays true if the feedstock ever adds a Windows build,
    and would then be proposed straight back. So the fleet's axis is tried
    first and its answer kept whenever it has one.

    The feedstock's own axis is the fallback, for the case where the fleet's
    produces no answer at all. `netcdf4` declares `numpy` on every machine
    conda-forge builds except `win-arm64`, which `netcdf4` does not build:
    against the fleet's eight targets that names seven, a group no selector
    spells, and the feedstock stopped. Against the six it renders, the marker
    holds on all of them and there is no condition to write.

    The grid is what makes the two axes one rule. A marker mixing them --
    `platform_system != "Windows" and python_version <= "3.12"` -- makes the
    answer vary along both, and the entries then say both: one condition per
    group of builds that agree, joined with `and` to the python run it holds
    over. `pyodps` writes exactly that by hand for `cython`, which is where the
    shape comes from.
    """
    if not variants:
        raise PlanError(f"no upstream declarations of {name!r} to split")

    minors = tuple(sorted(set(pythons))) or tuple(range(_CEILING))
    narrowed = _axis(targets)
    try:
        return _split(name, variants, constraint, minors, _ENVIRONMENTS)
    except PlanError:
        if narrowed == _ENVIRONMENTS:
            raise
    # The fleet's axis could not name a group. Ask again over the builds this
    # feedstock actually makes, and let its refusal stand if that fails too --
    # it names real builds rather than ones nobody renders.
    return _split(name, variants, constraint, minors, narrowed)


def _split(
    name: str,
    variants: Sequence[UpstreamRequirement],
    constraint: str | None,
    minors: Sequence[int],
    axis: tuple[_Env, ...],
) -> Split:
    """One pass over one axis, which is what `split_by_environment` retries."""
    markers = [(variant, parse_marker(variant, name)) for variant in variants]
    # Before anything is refused or rendered, drop what reaches no build at
    # all. A declaration gated below the oldest python this feedstock is built
    # for describes an artifact that does not exist, so refusing the feedstock
    # over what it says asks a maintainer to resolve a case that cannot arise.
    markers = [
        (variant, marker)
        for variant, marker in markers
        if _reaches(marker, minors, axis)
    ]
    if not markers:
        return Split(branches=(), complementary=False, considered=())

    for variant, marker in markers:
        if marker is not None:
            _refuse_other_axes(name, variant, marker)

    grid = {
        (minor, environment): _in_cell(name, markers, minor, environment, constraint)
        for minor in minors
        for environment in axis
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
        answers[(minor, environment)] != answers[(minors[0], environment)]
        for minor in minors
        for environment in axis
    )
    varies_by_environment = any(
        answers[(minor, environment)] != answers[(minor, axis[0])]
        for minor in minors
        for environment in axis
    )
    if varies_by_python and varies_by_environment:
        return _over_both(name, answers, considered, minors, axis)
    if varies_by_environment:
        return _over_environments(name, answers, considered, minors[0], axis)
    return _over_pythons(answers, considered, minors, axis)


def _reaches(
    marker: Marker | None, minors: Sequence[int], axis: Sequence[_Env]
) -> bool:
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
    # Everything outside the three axes is taken as true rather than evaluated,
    # or `packaging` would answer it from the interpreter running swage.
    admitted = optimistic(marker, PYTHON_AXIS | PLATFORM_AXIS | MACHINE_AXIS)
    return any(
        admitted.evaluate(_environment(minor, patch, environment))
        for minor in minors
        for environment in axis
        for patch in (0, 99)
    )


def _over_pythons(
    answers: Mapping[tuple[int, _Env], str | None],
    considered: tuple[UpstreamRequirement, ...],
    minors: Sequence[int],
    axis: Sequence[_Env],
) -> Split:
    """One branch per run of consecutive python releases that agree."""
    runs = _runs([(minor, answers[(minor, axis[0])]) for minor in minors])
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


def _over_environments(
    name: str,
    answers: Mapping[tuple[int, _Env], str | None],
    considered: tuple[UpstreamRequirement, ...],
    minor: int,
    axis: Sequence[_Env],
) -> Split:
    """One branch per group of builds that agree.

    The environment axis is a set rather than a line, so there is no run to
    merge -- the builds giving the same answer are grouped, and the group is
    named the way a recipe names it: `unix` for what is not Windows, `aarch64`
    for one machine, `linux and ppc64le` where it takes both (DESIGN.md 3.3.4).
    """
    groups: dict[str | None, list[_Env]] = {}
    for environment in axis:
        groups.setdefault(answers[(minor, environment)], []).append(environment)
    branches = tuple(
        Branch(_environment_condition(name, tuple(group), axis), specifier)
        for specifier, group in groups.items()
        if specifier is not None
    )
    return Split(
        branches=branches,
        complementary=len(branches) == 2 and len(groups) == 2,
        considered=considered,
    )


def _over_both(
    name: str,
    answers: Mapping[tuple[int, _Env], str | None],
    considered: tuple[UpstreamRequirement, ...],
    minors: Sequence[int],
    axis: Sequence[_Env],
) -> Split:
    """One branch per group of builds, per run of pythons that group agrees on.

    Builds are grouped by their *whole* answer across the python axis rather
    than by one release, so a group is a set of builds that behave alike
    everywhere -- and each group's runs are then the same computation
    `_over_pythons` does, with the group's own condition joined on.

    `pyodps` is the case this exists for. Upstream declares
    `cython>=3.0,<3.1; platform_system!='Windows' and python_version <= '3.12'`
    beside the same package bounded differently above 3.12, and its maintainer
    already writes the answer by hand as `if: not win and match(python,
    "<=3.12")`. swage refused the feedstock rather than write what the recipe
    it was reading already said.
    """
    by_answers: dict[tuple[str | None, ...], list[_Env]] = {}
    for environment in axis:
        key = tuple(answers[(minor, environment)] for minor in minors)
        by_answers.setdefault(key, []).append(environment)

    branches: list[Branch] = []
    for column, group in by_answers.items():
        if all(specifier is None for specifier in column):
            # Upstream asks for nothing on these builds, which is said by
            # writing no entry for them rather than by an empty branch.
            continue
        where = _environment_condition(name, tuple(group), axis)
        for start, end, specifier in _runs(list(zip(minors, column, strict=True))):
            if specifier is None:
                continue
            when = _python_condition(start, end, minors[0], minors[-1])
            branches.append(Branch(_joined(where, when), specifier))
    # `else:` pairs two halves of one axis, and there is no single axis here.
    return Split(branches=tuple(branches), complementary=False, considered=considered)


def _joined(where: str | None, when: str | None) -> str | None:
    """Both conditions as one, in the `and` form the fleet already writes."""
    if where is None:
        return when
    if when is None:
        return where
    return f"{where} and {when}"


def _in_cell(
    name: str,
    markers: Sequence[tuple[UpstreamRequirement, Marker | None]],
    minor: int,
    environment: _Env,
    constraint: str | None,
) -> tuple[tuple[UpstreamRequirement, ...], str | None]:
    """Which variants apply to one build, and what they add up to there.

    The specifier is None where upstream does not ask for the package on this
    build at all -- which is not the same as asking for it without a
    constraint, and conflating the two would put a bare dependency line into a
    recipe that should not carry one.
    """
    active = _active(name, markers, minor, environment)
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
    environment: _Env,
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
            marker.evaluate(_environment(minor, patch, environment))
            for patch in (0, 99)
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


#: The platforms conda-forge builds for, as a marker sees them. Shared with the
#: per-platform noarch model, which binds exactly these variables to exactly
#: these values, one platform at a time.
_PLATFORMS = ("linux", "osx", "win")
_AS_MARKER = PLATFORM_MARKERS

#: The machines conda-forge builds each platform for, spelled as a marker sees
#: them -- `aarch64` on linux and `arm64` on macOS are the same silicon under
#: two names, and Windows reports `AMD64` for what a recipe selects as
#: `x86_64`.
_MACHINES = {
    "linux": ("x86_64", "aarch64", "ppc64le", "s390x"),
    "osx": ("x86_64", "arm64"),
    "win": ("AMD64", "ARM64"),
}

#: One build target: a platform and a machine, which together are what
#: conda-forge calls a subdir and what a recipe's selectors name.
_Env = tuple[str, str]

_ENVIRONMENTS: tuple[_Env, ...] = tuple(
    (platform, machine) for platform in _PLATFORMS for machine in _MACHINES[platform]
)

#: conda-forge's subdir -> the build this module reasons about. The right-hand
#: names are a marker's rather than a subdir's: `linux-64` is `x86_64` to a
#: marker, and Windows reports `AMD64` for the same machine.
_TARGETS: dict[str, _Env] = {
    "linux-64": ("linux", "x86_64"),
    "linux-aarch64": ("linux", "aarch64"),
    "linux-ppc64le": ("linux", "ppc64le"),
    "linux-s390x": ("linux", "s390x"),
    "osx-64": ("osx", "x86_64"),
    "osx-arm64": ("osx", "arm64"),
    "win-64": ("win", "AMD64"),
    "win-arm64": ("win", "ARM64"),
}


def _axis(targets: Sequence[str]) -> tuple[_Env, ...]:
    """The builds to reason over: this feedstock's, or the fleet's.

    **The platform axis is a property of the feedstock** (DESIGN.md 3.3.1),
    and taking it as the fleet's is the same mistake as taking one output's
    build model for the whole tool's. `netcdf4` declares `numpy` on every
    machine conda-forge builds except `win-arm64` -- which `netcdf4` does not
    build -- so against the fleet's eight targets its markers name seven, a
    group no selector spells, and the feedstock stopped. Against the six it
    actually renders, the marker is true on all of them and there is no
    condition to write.

    The two targets that made the difference are close to fictional here: no
    feedstock in the fleet renders `linux-s390x` at all, and `win-arm64`
    appears in four variant files. They were nonetheless enough to refuse a
    feedstock whose answer was "unconditional".

    Empty ``targets`` keeps the fleet-wide axis, because that is what a caller
    with no rendered variants has to assume -- narrowing on absent data would
    invent a condition rather than read one. An unrecognized subdir is skipped
    for the same reason: conda-forge adding one should not silently shrink the
    axis to the ones this table happens to know.
    """
    known = tuple(_TARGETS[target] for target in targets if target in _TARGETS)
    return known or _ENVIRONMENTS


def _selects(axis: Sequence[_Env]) -> dict[str, frozenset[_Env]]:
    """Which of ``axis`` each selector a recipe can write is true of.

    The names are conda-forge's rather than a marker's: `arm64` covers Apple
    silicon and Windows on ARM, `x86_64` covers the machine Windows reports as
    `AMD64`, and `unix` is everything that is not Windows.

    Computed against the axis rather than the fleet, so a selector means what
    it means *on this feedstock*: `unix` on one that builds no Windows is
    every build there is, and `_environment_condition` then writes no
    condition at all instead of one that excludes nothing.
    """
    return {
        "linux": frozenset(env for env in axis if env[0] == "linux"),
        "osx": frozenset(env for env in axis if env[0] == "osx"),
        "win": frozenset(env for env in axis if env[0] == "win"),
        "unix": frozenset(env for env in axis if env[0] != "win"),
        "x86_64": frozenset(env for env in axis if env[1] in ("x86_64", "AMD64")),
        "aarch64": frozenset(env for env in axis if env[1] == "aarch64"),
        "arm64": frozenset(env for env in axis if env[1] in ("arm64", "ARM64")),
        "ppc64le": frozenset(env for env in axis if env[1] == "ppc64le"),
        "s390x": frozenset(env for env in axis if env[1] == "s390x"),
    }


#: The platform half of a condition, most readable first. `unix` before
#: `not win` because the fleet writes it 200 times against 173.
_WHERE = ("unix", "linux", "osx", "win", "not win", "not linux", "not osx")

#: And the machine half. Only ever appended to a platform, or used alone.
_MACHINE_SELECTORS = ("x86_64", "aarch64", "arm64", "ppc64le", "s390x")


def _environment(minor: int, patch: int, environment: _Env) -> dict[str, str]:
    platform, machine = environment
    return {
        "python_version": f"{_MAJOR}.{minor}",
        "python_full_version": f"{_MAJOR}.{minor}.{patch}",
        **_AS_MARKER[platform],
        "platform_machine": machine,
    }


def _selected(expression: str, axis: Sequence[_Env]) -> frozenset[_Env]:
    """The builds in ``axis`` one candidate condition is true of."""
    selects = _selects(axis)
    everything = frozenset(axis)
    selected = everything
    for term in expression.split(" and "):
        negated = term.startswith("not ")
        members = selects[term.removeprefix("not ")]
        selected &= everything - members if negated else frozenset(members)
    return selected


#: Every condition swage will write for a group of builds, in the order it
#: prefers them. Built once, because it depends on nothing but the axes.
_CANDIDATES: tuple[str, ...] = (
    *_WHERE,
    *_MACHINE_SELECTORS,
    *(
        f"{where} and {machine}"
        # A single platform first here, unlike above: `osx and arm64` names
        # Apple silicon, and `unix and arm64` names the same builds while
        # sounding like it covers more.
        for where in ("linux", "osx", "win", *_WHERE)
        for machine in _MACHINE_SELECTORS
    ),
)


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

    **`match` rather than a bare comparison, because a bare comparison is a
    string comparison.** A recipe's `if:` is evaluated by minijinja, where
    `python` is the variant's value as text, so `python < "3.13"` compares
    `"3.9"` against `"3.13"` character by character -- `'9' > '1'`, and python
    3.9 is excluded from a range it belongs to. rattler-build documents the
    trap and the answer in the same breath: "the comparison is a string
    comparison done by minijinja... use the `match` function to compare
    versions".

    swage wrote the bare form until it was caught rewriting
    `not win and match(python, "<=3.12")` into `unix and python < "3.13"` on
    `pyodps` -- replacing a maintainer's version-aware condition with a
    lexicographic one. It had survived because every minor conda-forge
    currently builds is two digits, and two-digit minors do compare correctly
    as strings; the bug was waiting for a floor below 3.10, not absent.

    The spelling was defended as "the way the fleet writes it", from a survey
    of what recipes contain rather than of what rattler-build means. The fleet
    writes both, and by a wide margin the correct one: 46 `match(python, ...)`
    against 13 bare comparisons.

    Open-ended wherever the run is, so a dependency upstream gates at one
    version reads as one comparison rather than as a window with a bound
    nobody wrote.

    ``floor`` and ``ceiling`` are the ends of the sampled axis: a run touching
    either is open-ended there, because there is no build beyond it to exclude.
    A run bounded by the oldest python a feedstock builds would otherwise
    render `match(python, ">=3.10")` -- true of every artifact, and read by the
    next person as a constraint upstream asked for.
    """
    below = f'match(python, "<{_MAJOR}.{end + 1}")'
    above = f'match(python, ">={_MAJOR}.{start}")'
    if start == floor:
        return None if end == ceiling else below
    return above if end == ceiling else f"{above} and {below}"


def _environment_condition(
    name: str, group: tuple[_Env, ...], axis: Sequence[_Env]
) -> str | None:
    """How a recipe names this group of builds, or a stop if it cannot.

    Tried against the selectors a recipe actually has rather than composed
    freely: a group is nameable when some condition selects exactly it, and a
    group that no condition selects is one swage would have to describe with a
    disjunction nobody writes by hand. That is a stop, and it says which builds
    it could not name rather than only that it could not.
    """
    wanted = frozenset(group)
    if wanted == frozenset(axis):
        return None
    for candidate in _CANDIDATES:
        if _selected(candidate, axis) == wanted:
            return candidate
    named = ", ".join(f"{platform}-{machine}" for platform, machine in sorted(group))
    raise PlanError(
        f"cannot write upstream's markers for {name!r} as build conditions\n"
        f"  they single out {named}, which no selector this recipe can carry "
        "names as a group\n"
        "  resolve by hand"
    )


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

    An arch output varies over python, over platform and over machine, and
    those are the axes a condition can key on -- conda-forge builds
    `linux-aarch64`, `osx-arm64` and `win-arm64` as surely as it builds
    `linux-64`, and a recipe selects them with `aarch64` and `arm64`.
    The Python implementation never reaches here: `parse_marker` has already
    fixed it to CPython, which is the only one conda-forge builds, so a
    condition on it is decided rather than refused.
    """
    other = sorted(
        marker_variables(marker) - PYTHON_AXIS - PLATFORM_AXIS - MACHINE_AXIS
    )
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

    Unlike the noarch case (DESIGN.md 3.3.2) this is not an artifact of one
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


#: Which platforms each selector a recipe can write covers. The platform half
#: of `_SELECTS`, kept separate because the per-platform noarch model varies
#: over platforms alone -- `noarch_platforms` lists whole subdirs, so there is
#: no machine axis inside one of its artifacts to select on.
_PLATFORM_SELECTS: dict[str, frozenset[str]] = {
    "unix": frozenset({"linux", "osx"}),
    "linux": frozenset({"linux"}),
    "osx": frozenset({"osx"}),
    "win": frozenset({"win"}),
    "not win": frozenset({"linux", "osx"}),
    "not linux": frozenset({"osx", "win"}),
    "not osx": frozenset({"linux", "win"}),
}


def split_by_platform(
    name: str,
    variants: Sequence[UpstreamRequirement],
    python_min: PythonMin,
    platforms: Sequence[str],
    feedstock: str | None = None,
    python_max: Version | None = None,
    constraint: str | None = None,
) -> tuple[Split, str | None]:
    """Write one dependency across the per-platform noarch packages built.

    The fourth build model: `noarch: python` and `noarch_platforms`, so
    conda-smithy builds the package once per listed platform. Each artifact is
    still installed on every python from the floor up, so the python axis
    collapses inside it exactly as it does for a single noarch package -- this
    asks `reconcile` once per platform and writes a condition only where the
    answers differ.

    A declaration carrying no platform marker therefore produces one plain
    line, not one per platform, which is what keeps this from adding structure
    to the great majority of a recipe that has no reason for it.

    The note comes back beside the split because it belongs to the collapse,
    and the collapse is per-platform: it is returned only where every platform
    agreed, since a note explaining a choice cannot be attached to branches
    that made different ones.
    """
    answers: dict[str, str | None] = {}
    notes: dict[str, str | None] = {}
    considered: list[UpstreamRequirement] = []
    for platform in platforms:
        result = reconcile(
            name,
            variants,
            python_min,
            feedstock,
            python_max,
            constraint=constraint,
            platform=platform,
        )
        answers[platform] = result.specifier if result.considered else None
        notes[platform] = result.note
        # A declaration applying on every platform is considered on every
        # platform, and the caller counts these as the declarations behind one
        # line rather than behind one artifact.
        considered.extend(
            variant for variant in result.considered if variant not in considered
        )

    if len(set(answers.values())) == 1:
        specifier = answers[platforms[0]]
        if specifier is None:
            return Split(branches=(), complementary=False, considered=()), None
        return (
            Split(
                branches=(Branch(None, specifier),),
                complementary=False,
                considered=tuple(considered),
            ),
            notes[platforms[0]],
        )

    groups: dict[str | None, set[str]] = {}
    for platform in platforms:
        groups.setdefault(answers[platform], set()).add(platform)

    branches = tuple(
        Branch(_platform_condition(members, platforms), specifier)
        for specifier, members in groups.items()
        if specifier is not None
    )
    return (
        Split(
            branches=branches,
            complementary=len(branches) == 2 and len(groups) == 2,
            considered=tuple(considered),
        ),
        None,
    )


def _platform_condition(members: set[str], built: Sequence[str]) -> str:
    """The selector naming exactly this group of platforms.

    Preferring a selector that means the group *everywhere* over one that only
    happens to mean it on the platforms this feedstock builds today. `colorlog`
    builds linux and win, so a dependency present only on linux is described
    equally well by `linux` and by `unix` -- and `unix` would quietly become
    wrong the day the feedstock adds osx. `linux` says what was meant.
    """
    for selector, covers in _PLATFORM_SELECTS.items():
        if covers == members:
            return selector
    for selector, covers in _PLATFORM_SELECTS.items():
        if covers & set(built) == members:
            return selector
    # Every group is a subset of three platforms, and the table covers every
    # non-empty subset bar the full set -- which cannot be a group, because a
    # group spanning every platform is the single-answer case above.
    raise PlanError(
        f"cannot name the platforms {sorted(members)} as a recipe condition"
    )
