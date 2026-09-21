"""One rule for upstream's markers, over a grid of builds (DESIGN.md §9.2-9.3).

A cell is one build: a Python minor and a target. An output's build model
says which cells form one artifact. Within an artifact the answer must hold
on every cell; across artifacts the recipe says what differs. `reconcile` is
that rule once, and `Artifacts` is the only thing that differs between the
noarch collapse, the per-platform noarch split and the arch translation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum

from packaging.markers import InvalidMarker, Marker
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from swage.upstream import UpstreamRequirement

from .errors import PlanError
from .markers import (
    MACHINE_AXIS,
    PLATFORM_AXIS,
    PLATFORM_MARKERS,
    PYTHON_AXIS,
    marker_variables,
    optimistic,
    resolve_implementation,
    summarize_python,
    without_axis,
)
from .python_min import PythonMin
from .specifiers import (
    declared_order,
    expand_compatible,
    parse_specifier,
    render_specifier,
    satisfiable,
)

__all__ = [
    "TARGETS",
    "Artifacts",
    "Branch",
    "Reconciled",
    "Target",
    "Universe",
    "parse_marker",
    "reconcile",
    "settled_already",
]

#: conda-forge builds python 3.
_MAJOR = 3

#: How far up the python axis to sample.
_HORIZON = 40

#: A build target: a platform and a machine, which together are a subdir.
Target = tuple[str, str]

#: One build: a python minor and a target.
Cell = tuple[int, Target]

_PLATFORMS = ("linux", "osx", "win")

#: The machines conda-forge builds each platform for, spelled as a marker sees
#: them (DESIGN.md §9.1).
_MACHINES = {
    "linux": ("x86_64", "aarch64", "ppc64le", "s390x"),
    "osx": ("x86_64", "arm64"),
    "win": ("AMD64", "ARM64"),
}

#: conda-forge's build targets, never a feedstock's rendered subset.
TARGETS: tuple[Target, ...] = tuple(
    (platform, machine) for platform in _PLATFORMS for machine in _MACHINES[platform]
)

#: The axes a marker can name and a build can vary over.
_MODELED = PYTHON_AXIS | PLATFORM_AXIS | MACHINE_AXIS


class Artifacts(Enum):
    """How an output's cells group into artifacts (DESIGN.md §9.3)."""

    ONE = "one"
    PER_PLATFORM = "per-platform"
    PER_CELL = "per-cell"


@dataclass(frozen=True)
class Universe:
    """The cells one output is built for, and how they group (DESIGN.md §9.2)."""

    artifacts: Artifacts
    pythons: tuple[int, ...]
    #: The platforms sampled: every one, or the rendered ones for a
    #: per-platform noarch output, whose artifacts those are.
    platforms: tuple[str, ...] = _PLATFORMS
    #: For the noarch contradiction message, which names the floor.
    python_min: PythonMin | None = None

    @classmethod
    def noarch(
        cls,
        python_min: PythonMin,
        python_max: Version | None = None,
        platforms: Sequence[str] = (),
    ) -> Universe:
        """One artifact from the floor up, or one per platform where given."""
        floor = python_min.version
        pythons = tuple(
            minor
            for minor in range(floor.minor, _HORIZON)
            if python_max is None
            or (python_max.major, python_max.minor) > (floor.major, minor)
        )
        if platforms:
            return cls(Artifacts.PER_PLATFORM, pythons, tuple(platforms), python_min)
        return cls(Artifacts.ONE, pythons, python_min=python_min)

    @classmethod
    def arch(cls, pythons: Sequence[int] = ()) -> Universe:
        """One artifact per cell, over the pythons `.ci_support` renders.

        Empty means the whole axis, which is what a feedstock with no rendered
        variants to read has to assume.
        """
        return cls(
            Artifacts.PER_CELL, tuple(sorted(set(pythons))) or tuple(range(_HORIZON))
        )

    @property
    def targets(self) -> tuple[Target, ...]:
        return tuple(target for target in TARGETS if target[0] in self.platforms)

    @property
    def cells(self) -> tuple[Cell, ...]:
        return tuple(
            (minor, target) for minor in self.pythons for target in self.targets
        )

    @property
    def expressible(self) -> frozenset[str]:
        """The marker variables a condition on this output can key on."""
        if self.artifacts is Artifacts.ONE:
            return PYTHON_AXIS
        if self.artifacts is Artifacts.PER_PLATFORM:
            return PYTHON_AXIS | PLATFORM_AXIS
        return _MODELED

    def artifact(self, cell: Cell) -> object:
        """Which artifact a cell belongs to, as a key."""
        if self.artifacts is Artifacts.ONE:
            return None
        if self.artifacts is Artifacts.PER_PLATFORM:
            return cell[1][0]
        return cell

    @property
    def artifact_keys(self) -> tuple[object, ...]:
        keys: dict[object, None] = {}
        for cell in self.cells:
            keys.setdefault(self.artifact(cell))
        return tuple(keys)


@dataclass(frozen=True)
class Branch:
    """One entry the recipe holds for a requirement."""

    #: The condition, e.g. ``match(python, "<3.13")``. None where the entry
    #: covers every artifact and the requirement is unconditional.
    condition: str | None
    #: The rendered specifier. Empty where upstream names the package without
    #: constraining it.
    specifier: str


@dataclass(frozen=True)
class Reconciled:
    """How one requirement is written across an output's artifacts."""

    entries: tuple[Branch, ...]
    #: Whether two entries partition the artifacts, in which case they are
    #: one `if:`/`then:`/`else:` entry rather than two.
    complementary: bool
    #: The comment above a collapsed line, naming why it says what it says.
    note: str | None
    #: Every declaration reachable on some cell. Empty where upstream asks for
    #: the package on no build, which is a removal for the planner to decide.
    considered: tuple[UpstreamRequirement, ...]
    #: Whether an `overruled_constraints` entry decided a line.
    overruled: bool = False

    @property
    def specifier(self) -> str:
        """The one unconditional line's specifier, or empty."""
        if len(self.entries) == 1 and self.entries[0].condition is None:
            return self.entries[0].specifier
        return ""


#: The two samples one cell is evaluated at, so that a marker distinguishing
#: patch releases of one minor is noticed.
_PATCHES = (0, 99)

_Samples = dict[Cell, tuple[bool, bool]]


def reconcile(
    name: str,
    variants: Sequence[UpstreamRequirement],
    universe: Universe,
    *,
    feedstock: str | None = None,
    constraint: str | None = None,
    built_everywhere: bool = False,
    overruled: str | None = None,
    output: str = "",
) -> Reconciled:
    """Write every declaration of ``name`` as what each artifact asks for.

    ``constraint`` is a bound config adds beyond upstream's; ``overruled`` is
    the bound one artifact states where upstream's declarations contradict
    each other; ``built_everywhere`` says the platform and machine markers are
    about upstream's wheel matrix; ``output`` is what the contradiction
    message names (DESIGN.md §9.3).
    """
    if not variants:
        raise PlanError(f"no upstream declarations of {name!r} to reconcile")

    declarations = [(variant, parse_marker(variant, name)) for variant in variants]
    if built_everywhere:
        declarations = [_without_wheel_matrix(v, m) for v, m in declarations]

    samples = [_sampled(marker, universe) for _, marker in declarations]
    reachable = [
        (declaration, sample)
        for declaration, sample in zip(declarations, samples, strict=True)
        if any(any(pair) for pair in sample.values())
    ]
    if not reachable:
        return Reconciled((), False, None, ())

    for (variant, marker), _ in reachable:
        if marker is not None:
            _refuse_inexpressible_axis(name, variant, marker, universe, feedstock)

    binding = reachable
    if built_everywhere:
        kept = _widest(
            name,
            [variant for (variant, _), _ in reachable],
            [_profile(sample, universe) for _, sample in reachable],
            feedstock,
        )
        binding = [reachable[index] for index in kept]

    answers: dict[object, str | None] = {}
    notes: dict[object, str | None] = {}
    settled = False
    for key in universe.artifact_keys:
        cells = [cell for cell in universe.cells if universe.artifact(cell) == key]
        asked = [
            variant
            for (variant, _), sample in binding
            if _asked(name, variant, sample, cells, universe)
        ]
        if not asked:
            answers[key], notes[key] = None, None
            continue
        partial = [
            variant
            for (variant, _), sample in binding
            if variant in asked and not _everywhere(sample, cells)
        ]
        answers[key], notes[key], settled_here = _collapse(
            name,
            asked,
            partial,
            universe,
            cells[0][0],
            feedstock,
            constraint,
            overruled,
            output,
        )
        settled = settled or settled_here

    considered = tuple(variant for (variant, _), _ in reachable)
    return _across_artifacts(name, universe, answers, notes, considered, settled)


def parse_marker(variant: UpstreamRequirement, name: str) -> Marker | None:
    """The declaration's marker with the implementation fixed to CPython."""
    if variant.marker is None:
        return None
    try:
        marker = Marker(variant.marker)
    except InvalidMarker as exc:
        raise PlanError(
            f"{name}: cannot parse marker {variant.marker!r}: {exc}"
        ) from exc
    return resolve_implementation(marker)


def _without_wheel_matrix(
    variant: UpstreamRequirement, marker: Marker | None
) -> tuple[UpstreamRequirement, Marker | None]:
    """The declaration with its platform and machine comparisons taken as true.

    `raw` is left alone, so an error still quotes what upstream wrote.
    """
    if marker is None:
        return variant, None
    folded = without_axis(marker, PLATFORM_AXIS | MACHINE_AXIS)
    text = None if folded is None else str(folded)
    return replace(variant, marker=text), folded


def _environment(cell: Cell, patch: int) -> dict[str, str]:
    minor, (platform, machine) = cell
    return {
        "python_version": f"{_MAJOR}.{minor}",
        "python_full_version": f"{_MAJOR}.{minor}.{patch}",
        **PLATFORM_MARKERS[platform],
        "platform_machine": machine,
    }


def _sampled(marker: Marker | None, universe: Universe) -> _Samples:
    """Where the marker holds, cell by cell, at both ends of each release.

    Comparisons on an axis swage does not model are taken as true, so that
    `packaging` never fills one from the interpreter running swage.
    """
    if marker is None:
        return dict.fromkeys(universe.cells, (True, True))
    admitted = optimistic(marker, _MODELED)
    return {
        cell: (
            admitted.evaluate(_environment(cell, _PATCHES[0])),
            admitted.evaluate(_environment(cell, _PATCHES[1])),
        )
        for cell in universe.cells
    }


def _profile(sample: _Samples, universe: Universe) -> tuple[bool, ...]:
    """Which builds a declaration is about, over the whole universe."""
    return tuple(held for cell in universe.cells for held in sample[cell])


def _asked(
    name: str,
    variant: UpstreamRequirement,
    sample: _Samples,
    cells: Sequence[Cell],
    universe: Universe,
) -> bool:
    """Whether this declaration reaches the artifact these cells make up.

    An artifact spanning several cells asks for the package if any of them
    does. One built per cell has to write a condition for exactly that cell,
    so a marker telling two patch releases of one minor apart is a stop.
    """
    if universe.artifacts is not Artifacts.PER_CELL:
        return any(any(sample[cell]) for cell in cells)
    (cell,) = cells
    first, second = sample[cell]
    if first != second:
        raise PlanError(
            f"cannot express upstream's marker for {name!r} as a build "
            "condition\n"
            f"    {_declaration(variant, name)} ; {variant.marker}\n"
            f"  it holds for some patch releases of python {_MAJOR}.{cell[0]} "
            "and not others, and conda-forge builds one package per minor "
            "release rather than one per patch\n"
            "  resolve by hand"
        )
    return first


def _everywhere(sample: _Samples, cells: Sequence[Cell]) -> bool:
    """Whether a declaration is active on every cell of an artifact.

    Such a declaration is unconditional as far as that artifact is concerned:
    the range it serves lies inside the marker, so the marker chooses nothing
    between the artifact's pythons and there is nothing for a note to explain.
    """
    return all(all(sample[cell]) for cell in cells)


def _collapse(
    name: str,
    asked: Sequence[UpstreamRequirement],
    partial: Sequence[UpstreamRequirement],
    universe: Universe,
    minor: int,
    feedstock: str | None,
    constraint: str | None,
    overruled: str | None,
    output: str,
) -> tuple[str, str | None, bool]:
    """One artifact's constraint, its note, and whether config overruled it.

    ``partial`` is the subset of ``asked`` active on only some of the
    artifact's cells: the declarations a note can name (DESIGN.md §9.3 step
    6). An artifact built per cell has no range to serve: a contradiction
    there is upstream contradicting itself on one build, which no bound in
    config can settle, and nothing is chosen, so there is no note.
    """
    combined = SpecifierSet()
    for variant in asked:
        combined &= parse_specifier(variant, name)

    if universe.artifacts is Artifacts.PER_CELL:
        if constraint is not None:
            combined &= SpecifierSet(constraint)
        if not satisfiable(combined):
            raise PlanError(_contradiction_on_build(name, asked, minor, constraint))
        return render_specifier(combined, declared_order(asked)), None, False

    settled = False
    if not satisfiable(combined):
        if overruled is None:
            raise PlanError(
                _contradiction(name, asked, universe.python_min, feedstock, output)
            )
        chosen = SpecifierSet(overruled)
        if not any(
            satisfiable(chosen & parse_specifier(variant, name)) for variant in asked
        ):
            raise PlanError(_unsupported(name, overruled, asked, feedstock))
        combined = chosen
        settled = True

    if constraint is not None:
        with_config = combined & SpecifierSet(constraint)
        if not satisfiable(with_config):
            raise PlanError(
                f"config constrains {name!r} to {constraint}, and no version "
                f"satisfying upstream's {combined} can meet it -- correct or "
                f"drop the config entry for {name!r}"
            )
        combined = with_config

    note = _overruled_note() if settled else _note(asked, partial)
    return render_specifier(combined, declared_order(asked)), note, settled


def _across_artifacts(
    name: str,
    universe: Universe,
    answers: Mapping[object, str | None],
    notes: Mapping[object, str | None],
    considered: tuple[UpstreamRequirement, ...],
    settled: bool,
) -> Reconciled:
    """The entries that say what differs between artifacts (DESIGN.md §9.3)."""
    if universe.artifacts is Artifacts.ONE:
        answer = answers[None]
        entries = () if answer is None else (Branch(None, answer),)
        return Reconciled(entries, False, notes[None], considered, settled)
    if universe.artifacts is Artifacts.PER_PLATFORM:
        return _over_platforms(name, universe, answers, notes, considered, settled)
    return _over_cells(name, universe, answers, considered)


def _over_platforms(
    name: str,
    universe: Universe,
    answers: Mapping[object, str | None],
    notes: Mapping[object, str | None],
    considered: tuple[UpstreamRequirement, ...],
    settled: bool,
) -> Reconciled:
    """One entry per group of platforms that agree.

    The note survives only where every platform agreed: a note explaining a
    choice cannot be attached to branches that made different ones.
    """
    platforms = universe.platforms
    if len({answers[platform] for platform in platforms}) == 1:
        specifier = answers[platforms[0]]
        if specifier is None:
            return Reconciled((), False, None, considered, settled)
        return Reconciled(
            (Branch(None, specifier),), False, notes[platforms[0]], considered, settled
        )
    groups: dict[str | None, set[str]] = {}
    for platform in platforms:
        groups.setdefault(answers[platform], set()).add(platform)
    branches = tuple(
        Branch(_platform_condition(members, platforms), specifier)
        for specifier, members in groups.items()
        if specifier is not None
    )
    return Reconciled(
        branches, len(branches) == 2 and len(groups) == 2, None, considered, settled
    )


def _over_cells(
    name: str,
    universe: Universe,
    answers: Mapping[object, str | None],
    considered: tuple[UpstreamRequirement, ...],
) -> Reconciled:
    """One entry per group of builds that agree, keyed on whichever axes vary."""
    minors = universe.pythons
    targets = universe.targets
    varies_by_python = any(
        answers[(minor, target)] != answers[(minors[0], target)]
        for minor in minors
        for target in targets
    )
    varies_by_target = any(
        answers[(minor, target)] != answers[(minor, targets[0])]
        for minor in minors
        for target in targets
    )
    if varies_by_python and varies_by_target:
        return _over_both(name, answers, considered, minors, targets)
    if varies_by_target:
        return _over_targets(name, answers, considered, minors[0], targets)
    return _over_pythons(answers, considered, minors, targets[0])


def _over_pythons(
    answers: Mapping[object, str | None],
    considered: tuple[UpstreamRequirement, ...],
    minors: Sequence[int],
    target: Target,
) -> Reconciled:
    """One entry per run of consecutive python releases that agree."""
    runs = _runs([(minor, answers[(minor, target)]) for minor in minors])
    branches = tuple(
        Branch(_python_condition(start, end, minors[0], minors[-1]), specifier)
        for start, end, specifier in runs
        if specifier is not None
    )
    return Reconciled(branches, len(branches) == 2 and len(runs) == 2, None, considered)


def _over_targets(
    name: str,
    answers: Mapping[object, str | None],
    considered: tuple[UpstreamRequirement, ...],
    minor: int,
    targets: Sequence[Target],
) -> Reconciled:
    """One entry per group of targets that agree, named as a recipe names it."""
    groups: dict[str | None, list[Target]] = {}
    for target in targets:
        groups.setdefault(answers[(minor, target)], []).append(target)
    branches = tuple(
        Branch(_target_condition(name, tuple(group)), specifier)
        for specifier, group in groups.items()
        if specifier is not None
    )
    return Reconciled(
        branches, len(branches) == 2 and len(groups) == 2, None, considered
    )


def _over_both(
    name: str,
    answers: Mapping[object, str | None],
    considered: tuple[UpstreamRequirement, ...],
    minors: Sequence[int],
    targets: Sequence[Target],
) -> Reconciled:
    """One entry per group of targets, per run of pythons that group agrees on.

    Targets are grouped by their whole answer across the python axis, so a
    group behaves alike everywhere, and each group's runs are joined to its
    condition with `and`. No `else:`: that pairs two halves of one axis.
    """
    by_answers: dict[tuple[str | None, ...], list[Target]] = {}
    for target in targets:
        column = tuple(answers[(minor, target)] for minor in minors)
        by_answers.setdefault(column, []).append(target)

    branches: list[Branch] = []
    for column, group in by_answers.items():
        if all(specifier is None for specifier in column):
            continue
        where = _target_condition(name, tuple(group))
        for start, end, specifier in _runs(list(zip(minors, column, strict=True))):
            if specifier is None:
                continue
            when = _python_condition(start, end, minors[0], minors[-1])
            branches.append(Branch(_joined(where, when), specifier))
    return Reconciled(tuple(branches), False, None, considered)


def _joined(where: str | None, when: str | None) -> str | None:
    if where is None:
        return when
    if when is None:
        return where
    return f"{where} and {when}"


def _runs(
    per_release: Sequence[tuple[int, str | None]],
) -> list[tuple[int, int, str | None]]:
    """Consecutive releases with the same answer, as ``(start, end, answer)``."""
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

    `match` rather than a bare comparison, which minijinja evaluates as a
    string comparison (docs/conda-forge.md). Open-ended at either end of the
    sampled axis, because there is no build beyond it to exclude.
    """
    below = f'match(python, "<{_MAJOR}.{end + 1}")'
    above = f'match(python, ">={_MAJOR}.{start}")'
    if start == floor:
        return None if end == ceiling else below
    return above if end == ceiling else f"{above} and {below}"


# --- naming a group of builds ----------------------------------------------

#: Which builds each selector a recipe can write is true of. The names are
#: conda-forge's: `arm64` covers Apple silicon and Windows on ARM, `x86_64`
#: covers the machine Windows reports as `AMD64`, `unix` is not Windows.
_SELECTS: dict[str, frozenset[Target]] = {
    "linux": frozenset(t for t in TARGETS if t[0] == "linux"),
    "osx": frozenset(t for t in TARGETS if t[0] == "osx"),
    "win": frozenset(t for t in TARGETS if t[0] == "win"),
    "unix": frozenset(t for t in TARGETS if t[0] != "win"),
    "x86_64": frozenset(t for t in TARGETS if t[1] in ("x86_64", "AMD64")),
    "aarch64": frozenset(t for t in TARGETS if t[1] == "aarch64"),
    "arm64": frozenset(t for t in TARGETS if t[1] in ("arm64", "ARM64")),
    "ppc64le": frozenset(t for t in TARGETS if t[1] == "ppc64le"),
    "s390x": frozenset(t for t in TARGETS if t[1] == "s390x"),
}

#: The platform half of a condition, most readable first: `unix` before
#: `not win` because the fleet writes it 200 times against 173.
_WHERE = ("unix", "linux", "osx", "win", "not win", "not linux", "not osx")

_MACHINE_SELECTORS = ("x86_64", "aarch64", "arm64", "ppc64le", "s390x")

#: Every condition swage will write for a group of builds, in the order it
#: prefers them: positive forms first, negated forms last (v1 §3.3.4). No
#: group is named by both, checked over all 92, so the order cannot change
#: an answer.
_CANDIDATES: tuple[str, ...] = (
    *_WHERE,
    *_MACHINE_SELECTORS,
    *(
        f"{where} and {machine}"
        for where in ("linux", "osx", "win", *_WHERE)
        for machine in _MACHINE_SELECTORS
    ),
    *(f"not {machine}" for machine in _MACHINE_SELECTORS),
    *(
        f"not ({where} and {machine})"
        for where in ("linux", "osx", "win")
        for machine in _MACHINE_SELECTORS
    ),
    *(
        f"not ({first} or {second})"
        for index, first in enumerate(_MACHINE_SELECTORS)
        for second in _MACHINE_SELECTORS[index + 1 :]
    ),
)


def _selected(expression: str) -> frozenset[Target]:
    """The builds one candidate condition is true of."""
    everything = frozenset(TARGETS)
    if expression.startswith("not ("):
        return everything - _group(expression[len("not (") : -1])
    selected = everything
    for term in expression.split(" and "):
        negated = term.startswith("not ")
        members = _SELECTS[term.removeprefix("not ")]
        selected &= everything - members if negated else frozenset(members)
    return selected


def _group(inner: str) -> frozenset[Target]:
    if " or " in inner:
        return frozenset().union(*(_SELECTS[term] for term in inner.split(" or ")))
    return frozenset.intersection(*(_SELECTS[term] for term in inner.split(" and ")))


def _target_condition(name: str, group: tuple[Target, ...]) -> str | None:
    """How a recipe names this group of builds, or a stop if it cannot."""
    wanted = frozenset(group)
    if wanted == frozenset(TARGETS):
        return None
    for candidate in _CANDIDATES:
        if _selected(candidate) == wanted:
            return candidate
    named = ", ".join(f"{platform}-{machine}" for platform, machine in sorted(group))
    raise PlanError(
        f"cannot write upstream's markers for {name!r} as build conditions\n"
        f"  they single out {named}, which no selector this recipe can carry "
        "names as a group\n"
        "  resolve by hand"
    )


#: The platform half of `_SELECTS`, for the per-platform noarch model:
#: `noarch_platforms` lists whole subdirs, so there is no machine axis inside
#: one of its artifacts to select on.
_PLATFORM_SELECTS: dict[str, frozenset[str]] = {
    "unix": frozenset({"linux", "osx"}),
    "linux": frozenset({"linux"}),
    "osx": frozenset({"osx"}),
    "win": frozenset({"win"}),
    "not win": frozenset({"linux", "osx"}),
    "not linux": frozenset({"osx", "win"}),
    "not osx": frozenset({"linux", "win"}),
}


def _platform_condition(members: set[str], built: Sequence[str]) -> str:
    """The selector naming exactly this group of platforms.

    A selector that means the group everywhere is preferred over one that only
    happens to mean it on the platforms this feedstock builds today.
    """
    for selector, covers in _PLATFORM_SELECTS.items():
        if covers == members:
            return selector
    for selector, covers in _PLATFORM_SELECTS.items():
        if covers & set(built) == members:
            return selector
    raise PlanError(
        f"cannot name the platforms {sorted(members)} as a recipe condition"
    )


# --- refusals and messages ---------------------------------------------------


def _config_target(feedstock: str | None) -> str:
    return (
        f"config/feedstocks/{feedstock}.yaml"
        if feedstock
        else "this feedstock's config file"
    )


def _declaration(variant: UpstreamRequirement, name: str) -> str:
    return f"{name}{variant.specifier}"


def _refuse_inexpressible_axis(
    name: str,
    variant: UpstreamRequirement,
    marker: Marker,
    universe: Universe,
    feedstock: str | None,
) -> None:
    """Stop on a marker naming an axis this output's conditions cannot key on.

    Both resolutions are named where two exist, because "swage cannot do
    this" and "swage will not choose this for you" send the reader somewhere
    different, and only the second is true (v1 §3.3.4).
    """
    other = sorted(marker_variables(marker) - universe.expressible)
    if not other:
        return
    declaration = variant.raw or f"{name} {variant.specifier}; {variant.marker}"
    if universe.artifacts is Artifacts.PER_CELL:
        raise PlanError(
            f"cannot write upstream's marker for {name!r} as a build condition\n"
            f"    {_declaration(variant, name)} ; {variant.marker}\n"
            f"  it turns on {', '.join(other)}, which is not something this "
            "output is built once for each of\n"
            "  resolve by hand"
        )
    if universe.artifacts is Artifacts.PER_PLATFORM:
        raise PlanError(
            f"build-conditional constraint for {name!r}\n"
            f"    {declaration}\n"
            f"  the marker turns on {', '.join(other)}, which does not vary "
            "across one of the per-platform noarch packages this feedstock "
            "builds\n"
            "  where conda-forge builds " + name + " for every target this "
            "package is built for, the\n"
            "  marker is about upstream's own wheels rather than about where "
            + name
            + " is\n  needed -- record that in built_everywhere in "
            + _config_target(feedstock)
            + "\n  otherwise resolve by hand"
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
        "  a third answer applies where conda-forge builds " + name + " for "
        "every target this\n"
        "  package is built for: the marker is then about upstream's own "
        "wheels rather than\n  about where " + name + " is needed, and "
        "recording that in built_everywhere in\n  "
        + _config_target(feedstock)
        + " writes one plain line\n"
        "  otherwise resolve by hand"
    )


def _widest(
    name: str,
    variants: Sequence[UpstreamRequirement],
    profiles: Sequence[tuple[bool, ...]],
    feedstock: str | None,
) -> list[int]:
    """Drop a declaration a less constrained sibling about the same builds covers.

    Once the wheel-matrix axes are erased, two declarations about the same
    builds are alternatives rather than conjuncts, and intersecting them
    manufactures a contradiction out of two satisfiable constraints
    (v1 §3.3.4.1). Widest means containing: a constraint is dropped when
    another states a subset of its clauses. Neither containing the other is a
    stop.
    """
    groups: dict[tuple[bool, ...], list[int]] = {}
    for index, profile in enumerate(profiles):
        groups.setdefault(profile, []).append(index)

    overruled: set[int] = set()
    for members in groups.values():
        if len(members) > 1:
            overruled |= set(members) - {
                _least_constrained(name, variants, members, feedstock)
            }
    return [index for index in range(len(variants)) if index not in overruled]


def _least_constrained(
    name: str,
    variants: Sequence[UpstreamRequirement],
    members: Sequence[int],
    feedstock: str | None,
) -> int:
    clauses = {
        index: frozenset(
            str(clause) for clause in SpecifierSet(variants[index].specifier)
        )
        for index in members
    }
    for index in members:
        if all(clauses[index] <= clauses[other] for other in members):
            return index
    raise PlanError(_no_widest(name, [variants[index] for index in members], feedstock))


def _no_widest(
    name: str, variants: Sequence[UpstreamRequirement], feedstock: str | None
) -> str:
    quoted = "\n".join(
        f"    {variant.raw or _declaration(variant, name)}" for variant in variants
    )
    target = _config_target(feedstock)
    return (
        f"no widest constraint for {name!r}\n"
        f"{quoted}\n"
        f"  {name} is configured as one conda-forge builds everywhere, so these "
        "declarations\n"
        "  are about the same builds -- but neither admits everything the other "
        "does,\n"
        "  so there is no widest one to take\n"
        f"  resolve by hand, or drop the built_everywhere entry for {name!r} in "
        f"{target}"
    )


def _contradiction(
    name: str,
    variants: Sequence[UpstreamRequirement],
    python_min: PythonMin | None,
    feedstock: str | None,
    output: str = "",
) -> str:
    """Two declarations one artifact cannot serve across its range (v1 §3.3.2)."""
    width = max(len(_declaration(v, name)) for v in variants)
    quoted = "\n".join(
        f"    {_declaration(v, name):<{width}}" + (f" ; {v.marker}" if v.marker else "")
        for v in variants
    )
    target = _config_target(feedstock)
    where = f" in {output}" if output else ""
    floor = (
        f">={python_min.value} (python_min, from {python_min.source})"
        if python_min is not None
        else "the floor up"
    )
    return (
        f"contradictory upstream constraints for {name!r}{where}\n"
        f"{quoted}\n"
        f"  no single version satisfies them all across python {floor},\n"
        "  and conda-forge builds one noarch package for all of them\n"
        f"  resolve by hand, or record which bound this package states under "
        f"`overruled_constraints` in {target}"
    )


def _contradiction_on_build(
    name: str,
    active: Sequence[UpstreamRequirement],
    minor: int,
    constraint: str | None,
) -> str:
    """Two declarations that hold on the same build and cannot both be met."""
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


def _unsupported(
    name: str,
    overruled: str,
    variants: Sequence[UpstreamRequirement],
    feedstock: str | None,
) -> str:
    quoted = "\n".join(f"    {_declaration(v, name)}" for v in variants)
    return (
        f"`overruled_constraints` states {overruled} for {name!r}, which no "
        "version upstream asks for can meet:\n"
        f"{quoted}\n"
        "  an entry here chooses between what upstream declares, so upstream "
        "moving is a\n"
        "  reason to revisit it rather than something to render past -- "
        f"correct or drop it in {_config_target(feedstock)}"
    )


def settled_already(name: str, feedstock: str | None) -> str:
    """An overruling bound for declarations that have stopped disagreeing.

    Raised by the caller, which alone knows whether any artifact used it.
    """
    return (
        f"`overruled_constraints` states a bound for {name!r}, and upstream's "
        "declarations of it no longer contradict each other\n"
        "  there is nothing left for the entry to settle, so it would be "
        "quietly overriding upstream\n"
        f"  instead -- drop it in {_config_target(feedstock)}"
    )


def _overruled_note() -> str:
    return (
        "upstream's bound varies by python; this package is built once for all of them"
    )


# --- the note above a collapsed line ------------------------------------------

_LOWER_BOUND_OPERATORS = frozenset({">=", ">", "=="})
_UPPER_BOUND_OPERATORS = frozenset({"<=", "<"})


def _note(
    reachable: Sequence[UpstreamRequirement],
    partial: Sequence[UpstreamRequirement],
) -> str | None:
    """Name the markers behind the bounds that ended up binding (v1 §3.3.1).

    Both ends are named where they came from different declarations. Only a
    ``partial`` declaration -- one active on some of the artifact's pythons
    and not others -- has a marker worth naming: one that holds on all of
    them says nothing this note exists to say. `virtualenv` 21.9.0 asks for
    `hatchling >=1.28` on python >=3.10 and the feedstock builds for >=3.11,
    so the floor is not tighter than upstream's on any package it builds.
    """
    ends = (
        ("floors", _binding(reachable, _floor, most=max)),
        ("ceilings", _binding(reachable, _ceiling, most=min)),
        ("exclusions", _excluding(reachable)),
    )
    named: list[tuple[str, str]] = []
    for label, variant in ends:
        marker = _marker_of(variant, partial)
        if marker is not None and marker not in [seen for _, seen in named]:
            named.append((label, marker))
    if not named:
        return None
    return "tightest of upstream's " + " and ".join(
        f"{label} ({marker})" for label, marker in named
    )


def _binding(
    reachable: Sequence[UpstreamRequirement],
    bound: Callable[[UpstreamRequirement], Version | None],
    most: Callable[[Version, Version], Version],
) -> UpstreamRequirement | None:
    """The declaration whose bound survives the intersection.

    Several declarations all stating the same bound select nothing between
    them, so no marker is behind that end. A single declaration is still
    named: its marker says upstream asks only on some pythons while one
    artifact carries the line on all of them.
    """
    stated = [
        (variant, version)
        for variant, version in ((v, bound(v)) for v in reachable)
        if version is not None
    ]
    if not stated:
        return None
    binding, winning = stated[0]
    for variant, version in stated[1:]:
        if most(version, winning) != winning:
            winning, binding = version, variant
    if (
        len(stated) > 1
        and len(stated) == len(reachable)
        and all(version == winning for _, version in stated)
    ):
        return None
    return binding


def _excluding(
    reachable: Sequence[UpstreamRequirement],
) -> UpstreamRequirement | None:
    """The declaration excluding a version the others do not."""
    excluded = {
        index: frozenset(
            str(clause)
            for clause in SpecifierSet(variant.specifier)
            if clause.operator == "!="
        )
        for index, variant in enumerate(reachable)
    }
    for index, variant in enumerate(reachable):
        if any(
            excluded[index] - excluded[other] for other in excluded if other != index
        ):
            return variant
    return None


def _marker_of(
    variant: UpstreamRequirement | None, partial: Sequence[UpstreamRequirement]
) -> str | None:
    if variant is None or variant.marker is None or variant not in partial:
        return None
    marker = resolve_implementation(Marker(variant.marker))
    if marker is None:
        return None
    return summarize_python(marker)


def _floor(variant: UpstreamRequirement) -> Version | None:
    return _bound(variant, _LOWER_BOUND_OPERATORS, max)


def _ceiling(variant: UpstreamRequirement) -> Version | None:
    return _bound(variant, _UPPER_BOUND_OPERATORS, min)


def _bound(
    variant: UpstreamRequirement,
    operators: frozenset[str],
    most: Callable[[list[Version]], Version],
) -> Version | None:
    versions: list[Version] = []
    for clause in expand_compatible(SpecifierSet(variant.specifier)):
        if clause.operator not in operators:
            continue
        try:
            versions.append(Version(clause.version.rstrip(".*")))
        except InvalidVersion:
            continue
    return most(versions) if versions else None
