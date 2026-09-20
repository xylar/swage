"""One output's build model, computed once and passed as one value (DESIGN.md §9.1).

Everything the build model implies -- which pythons and targets the output is
built for, how its cells group into artifacts, what it draws from upstream and
which of its sections swage plans -- is derived here, per output, before any
requirement is looked at. `plan_section` then takes the `Output` in place of
the dozen parameters that used to describe it.

The derivation reads three things. The recipe says whether the output is
noarch, what it caps python at and whether it cross-compiles. `.ci_support`
says which pythons and platforms conda-smithy renders and which variant keys
the global pinning supplies; the caller reads it, so this stays a function of
values. Config says what the output draws on (`output_roles`) and which pieces
of a split extra it takes (`output_selections`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from packaging.version import Version

from swage.config import FeedstockConfig
from swage.mapping import normalize_name
from swage.recipe import Recipe, RecipeOutput, RequirementsBlock

from .errors import PlanError
from .grid import Artifacts, Target, Universe
from .prose import output_phrase
from .python_min import PythonMin, python_ceiling

__all__ = [
    "PLANNED_SECTIONS",
    "Output",
    "build_universe",
    "derive_outputs",
    "output_roles",
    "output_selections",
]

#: The only sections swage plans today. `run_constraints` is read, never
#: authored (design-v1.md 3.3.9). `build` is a longer story: most of it is
#: compilers and cross-compilation helpers that answer no question upstream
#: metadata asks, but a cross-compilation block also repeats `host`'s
#: upstream-derived entries. Whether a requirement belongs in one is open
#: (design-v1.md 3.3.6.1); keeping a copy that is already there in step with the
#: line it copies is not, and `_mirrors` does that without planning the
#: section.
PLANNED_SECTIONS = ("host", "run")

#: How the fleet says "this build runs on one platform and targets another".
#: Every one of the 19 outputs in the maintainer's checkouts with such a block
#: writes the condition this way, alone or joined to an mpi variant with `and`.
_CROSS = "build_platform != target_platform"


@dataclass(frozen=True)
class Output:
    """One output of a recipe, and everything its build model implies.

    ``name`` is what a report calls the output and ``package`` is what config's
    `outputs` matches. They are the same string almost always and are not the
    same question: an output that only stages -- `gdal`'s `core-build` -- has
    requirements to report on and no package to match config against, so its
    ``package`` is None.
    """

    name: str
    package: str | None
    noarch: bool
    artifacts: Artifacts
    #: The python minors the output is built for. Empty on a noarch output
    #: with no floor, which is the stop `floor` raises.
    pythons: tuple[int, ...]
    targets: tuple[Target, ...]
    #: The floor a noarch output collapses its markers over, and which file
    #: said so. None where nothing declares one, which is not an error on an
    #: architecture-specific output (design-v1.md 3.3.3).
    python_floor: PythonMin | None
    #: The python this output is *not* built for, where its recipe caps one.
    python_ceiling: Version | None
    #: The variant keys `.ci_support` covers, which a `host` line takes no
    #: bound for (design-v1.md 3.3.6).
    pinned: frozenset[str]
    #: Whether `build` has a block that runs on one platform and targets
    #: another, whose copies of `host` lines `_mirrors` keeps in step.
    cross_compiled: bool
    #: Whether this output draws upstream's runtime dependencies.
    core: bool
    #: The extras drawn whole, in config's order, which is the order their
    #: lines take.
    extras: tuple[str, ...]
    #: Extra -> the packages of it this output takes, for an extra split
    #: across outputs. An extra folded in whole is absent, meaning all of it.
    from_extras: Mapping[str, frozenset[str]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    #: The requirements blocks swage plans, in `PLANNED_SECTIONS` order.
    sections: tuple[RequirementsBlock, ...] = ()

    @property
    def floor(self) -> PythonMin:
        """The floor a noarch output collapses its markers over, or the stop.

        Demanded here rather than where it was resolved, because this is where
        it is known that an output needed one (design-v1.md 3.3.3) -- and
        demanded whether or not upstream declares any dependency this version,
        so that a feedstock conda-smithy has never rendered says so at every
        version rather than only at the ones with a marker in them.
        """
        if self.python_floor is not None:
            return self.python_floor
        where = output_phrase(self.name)
        raise PlanError(
            f"cannot determine the python floor {where} is built from\n"
            "  it builds one noarch package installed on every python from that "
            "floor up, so the floor is both what ${{ python_min }} expands to and "
            "the bottom of the range upstream's python markers are read over\n"
            "  the recipe sets no context.python_min and no .ci_support file "
            "declares one -- run conda-smithy on this feedstock, or set "
            "context.python_min in the recipe"
        )

    @property
    def platforms(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(platform for platform, _ in self.targets))

    @property
    def universe(self) -> Universe:
        """The cells this output is built for, and how they group (DESIGN.md §9.2)."""
        if not self.noarch:
            return Universe(self.artifacts, self.pythons, self.platforms)
        return Universe(self.artifacts, self.pythons, self.platforms, self.floor)

    @property
    def built_for(self) -> str:
        """Which pythons this output is built for, said as a recipe says it.

        The two build models answer it differently and both answers are the
        plain truth about the artifacts. One noarch package is installed across
        a range, so the range is what a marker is read against; an
        architecture-specific output is built once per python, so the list of
        them is.
        """
        if not self.noarch:
            return "python " + ", ".join(f"3.{minor}" for minor in self.pythons)
        ceiling = f",<{self.python_ceiling}" if self.python_ceiling is not None else ""
        return f"python >={self.floor.version}{ceiling}"


def derive_outputs(
    recipe: Recipe,
    config: FeedstockConfig,
    python_min: PythonMin | None,
    pythons: Sequence[int] = (),
    platforms: Sequence[str] = (),
    pinned: frozenset[str] = frozenset(),
) -> tuple[Output, ...]:
    """One `Output` per recipe output, in the recipe's order.

    ``python_min`` is None where neither the recipe nor `.ci_support` declares
    one, which is conda-smithy's answer for a feedstock building no noarch
    python package. The demand for it is made per output, by `Output.floor`,
    because that is the only place it is known whether one was needed
    (design-v1.md 3.3.3).

    ``pythons`` is the other half of the same answer, and the one an
    architecture-specific output needs: the minor releases `.ci_support` says
    this feedstock is built for. A noarch output collapses its markers over a
    range starting at `python_min`; an arch output is built once per release in
    this set, and a declaration reaching none of them describes an artifact
    that does not exist.

    ``platforms`` is the third, and it is what tells the two noarch models
    apart: one platform is the ordinary single artifact, and more than one
    means conda-smithy is building the package once per platform, so a marker
    naming the platform becomes a condition instead of a refusal.
    """
    roles = output_roles(recipe, config)
    selections = output_selections(config)
    return tuple(
        _derive(
            output,
            roles.get(output.name or "", ((), True)),
            selections.get(output.name or "", {}),
            python_min,
            pythons,
            platforms,
            pinned,
        )
        for output in recipe.outputs
    )


def _derive(
    output: RecipeOutput,
    role: tuple[tuple[str, ...], bool],
    from_extras: Mapping[str, frozenset[str]],
    python_min: PythonMin | None,
    pythons: Sequence[int],
    platforms: Sequence[str],
    pinned: frozenset[str],
) -> Output:
    # The build model, per output, because that is what it is a property of
    # (DESIGN.md §1): `sqlalchemy` is a compiled base output beside noarch
    # metapackages and `apache-beam` is a compiled base output beside eleven
    # noarch ones.
    noarch = output.noarch == "python"
    # Per output too, because the cap is stated on that output's own `python`
    # line and a split recipe may cap one package and not another.
    ceiling = python_ceiling(output)
    universe = build_universe(noarch, python_min, ceiling, pythons, platforms)
    build = output.blocks.get("build")
    extras, core = role
    return Output(
        name=output.label,
        package=output.name,
        noarch=noarch,
        artifacts=universe.artifacts,
        pythons=universe.pythons,
        targets=universe.targets,
        python_floor=python_min if noarch else None,
        python_ceiling=ceiling,
        pinned=pinned,
        cross_compiled=build is not None
        and any(_CROSS in entry.condition for entry in build.content.conditionals),
        core=core,
        extras=extras,
        from_extras=from_extras,
        sections=tuple(
            block
            for name in PLANNED_SECTIONS
            if (block := output.blocks.get(name)) is not None
        ),
    )


def build_universe(
    noarch: bool,
    python_min: PythonMin | None,
    python_ceiling: Version | None,
    pythons: Sequence[int] = (),
    platforms: Sequence[str] = (),
) -> Universe:
    """The cells an output with this build model is built for (DESIGN.md §9.1).

    An architecture-specific output is built once per cell, over the pythons
    `.ci_support` renders. A noarch one is built once, from the floor to the
    ceiling -- or once per platform where conda-smithy renders more than one,
    and then those platforms are its targets, because each is an artifact and
    a platform it does not render has none. Otherwise the targets are every
    one conda-forge builds, never the feedstock's rendered subset (v1 §3.3.4).

    A noarch output with no floor has no range to sample. The stop is
    `Output.floor`, raised where a section is planned, so that an output with
    nothing to plan is not refused for a floor it never needed.
    """
    if not noarch:
        return Universe.arch(pythons)
    if python_min is None:
        return Universe(Artifacts.ONE, ())
    return Universe.noarch(
        python_min, python_ceiling, platforms if len(platforms) > 1 else ()
    )


def output_roles(
    recipe: Recipe, config: FeedstockConfig
) -> dict[str, tuple[tuple[str, ...], bool]]:
    """What each output draws on: its extras, and whether it takes core deps.

    Two config shapes express this and a feedstock may use both (design-v1.md 4).
    `outputs[].run` folds extras into an existing output -- the google-cloud
    shape. `extras_as_outputs` publishes each extra as an output of its own --
    the airflow shape -- and those outputs are metapackages: they carry the
    extra's dependencies and a `pin_subpackage` back to the real package, and
    take none of upstream's own dependencies. Handing them core would add every
    runtime dependency to a package that installs nothing.

    An output named by neither takes core and no extras, which is what a
    single-output feedstock wants.

    **`{name}` in the suffix is the package's name, and that is not the
    feedstock's.** `apache-airflow-core-split-feedstock` builds
    `apache-airflow-core`, so formatting the suffix with the feedstock name
    yields `apache-airflow-core-split-with-async` -- a key matching no output
    the recipe has. Nothing would report it, either: the roles simply fail to
    match, every published extra output falls back to "core, no extras", and
    swage plans a metapackage as though it were the library it wraps. The name
    therefore comes from the recipe's own `context.name`, which is what the
    `${{ name }}` in its output names resolves to, so the generated key matches
    by construction rather than by the two names happening to coincide.
    """
    roles: dict[str, tuple[tuple[str, ...], bool]] = {}

    extras_as_outputs = config.extras_as_outputs
    if extras_as_outputs is not None:
        # The feedstock name only where the recipe sets no `context.name`, in
        # which case its outputs are named literally and there is nothing
        # better to go on.
        package = recipe.context.get("name", config.feedstock)
        for extra in extras_as_outputs.supported:
            name = extras_as_outputs.suffix.format(name=package, extra=extra)
            roles[name] = ((extra,), False)

    for name, output in config.outputs.items():
        # A split extra is drawn on by this output exactly as a whole one is:
        # its lines carry the same provenance and it is accounted for at G3.
        # Which *packages* of it this output takes is `output_selections`.
        drawn = tuple(output.run.extras) + tuple(output.run.from_extras)
        roles[name] = (drawn, output.run.core)

    return roles


def output_selections(config: FeedstockConfig) -> dict[str, dict[str, frozenset[str]]]:
    """Output name -> extra -> the packages of it that output takes.

    Only extras a feedstock splits across outputs appear here. An extra folded
    in whole says nothing, which is what the empty mapping means downstream:
    take all of it (design-v1.md 4).
    """
    return {
        name: {
            extra: frozenset(normalize_name(package) for package in packages)
            for extra, packages in output.run.from_extras.items()
        }
        for name, output in config.outputs.items()
        if output.run.from_extras
    }
