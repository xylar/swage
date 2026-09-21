"""One output's build model, computed once and passed as one value (DESIGN.md
§9.1).

The derivation reads the recipe (noarch, the python ceiling, cross-compilation),
`.ci_support` as the caller read it (pythons, platforms, pinned keys), and
config (`output_roles`, `output_selections`).
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

#: The only sections swage plans. `run_constraints` is read, never authored (v1
#: §3.3.9); `build` is read for cross-compilation and its copies kept in step by
#: `_mirrors`, never planned (v1 §3.3.6.1).
PLANNED_SECTIONS = ("host", "run")

#: How a recipe says this build runs on one platform and targets another.
_CROSS = "build_platform != target_platform"


@dataclass(frozen=True)
class Output:
    """One output of a recipe, and everything its build model implies.

    ``name`` is what a report calls the output; ``package`` is what config's
    `outputs` matches, and None for an output that only stages.
    """

    name: str
    package: str | None
    noarch: bool
    artifacts: Artifacts
    #: The python minors the output is built for. Empty on a noarch output
    #: with no floor, which is the stop `floor` raises.
    pythons: tuple[int, ...]
    targets: tuple[Target, ...]
    #: The floor a noarch output collapses its markers over, and which file said
    #: so. None is not an error on an arch output (v1 §3.3.3).
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

        Demanded here, where it is known an output needed one (v1 §3.3.3),
        whether or not upstream declares a marker this version.
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
        """Which pythons this output is built for, said as a recipe says it: a
        range for noarch, a list for an arch output.
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
    """One `Output` per recipe output, in the recipe's order (DESIGN.md §9.1).

    ``python_min`` is None where neither the recipe nor `.ci_support`
    declares one; `Output.floor` demands it per output. ``pythons`` is the
    minors `.ci_support` renders, for an arch output. ``platforms`` tells the
    two noarch models apart.
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
    # The build model, per output (DESIGN.md §1).
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

    A noarch output with no floor has no range to sample; the stop is
    `Output.floor`, raised where a section is planned.
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

    `outputs[].run` folds extras into an existing output; `extras_as_outputs`
    publishes each as a metapackage taking no core (v1 §4). An output named
    by neither takes core and no extras. `{name}` in the suffix is the
    recipe's `context.name`, which is what its output names resolve to.
    """
    roles: dict[str, tuple[tuple[str, ...], bool]] = {}

    extras_as_outputs = config.extras_as_outputs
    if extras_as_outputs is not None:
        # The feedstock name only where the recipe sets no `context.name`.
        package = recipe.context.get("name", config.feedstock)
        for extra in extras_as_outputs.supported:
            name = extras_as_outputs.suffix.format(name=package, extra=extra)
            roles[name] = ((extra,), False)

    for name, output in config.outputs.items():
        # A split extra is drawn on as a whole one is; which packages of it this
        # output takes is `output_selections`.
        drawn = tuple(output.run.extras) + tuple(output.run.from_extras)
        roles[name] = (drawn, output.run.core)

    return roles


def output_selections(config: FeedstockConfig) -> dict[str, dict[str, frozenset[str]]]:
    """Output name -> extra -> the packages of it that output takes.

    Only split extras appear; an empty mapping means take all of it (v1 §4).
    """
    return {
        name: {
            extra: frozenset(normalize_name(package) for package in packages)
            for extra, packages in output.run.from_extras.items()
        }
        for name, output in config.outputs.items()
        if output.run.from_extras
    }
