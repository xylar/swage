"""Turn a recipe, its upstream metadata and the quirks database into a plan.

`plan_section` is the core computation of DESIGN.md 3.3, and it is assembled
from the pieces around it rather than reimplementing any of them: `reconcile`
collapses a package's marker variants, `attribute` explains what is already in
the recipe, `classify_removal` decides what may leave, and `order_requirements`
puts the result in the order swage writes.

The order of operations matters in one place. What upstream wants is computed
first and the recipe's existing lines are folded in afterwards, so a line
upstream still declares is *replaced* by the reconciled version rather than
kept as it was. A line upstream does not declare survives only if removing it
would destroy something (DESIGN.md 3.3.7).

**Nothing here authors a `run_constraints` entry**, and there is deliberately
no code path that could. The section is checked for association (3.3.9) and
otherwise left exactly as found.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from swage.config import FeedstockConfig
from swage.mapping import NameResolver
from swage.recipe import Recipe, RequirementsBlock
from swage.upstream import UpstreamMetadata, UpstreamRequirement

from .attribute import (
    Provenance,
    Unexplained,
    attribute,
    build_index,
)
from .constrained import UnassociatedConstraint, check_run_constraints
from .lines import parse_line
from .model import PlannedRequirement
from .order import order_requirements
from .python_min import PythonMin
from .reconcile import reconcile
from .removals import Removal, classify_removal

__all__ = [
    "PlannedSection",
    "RecipePlan",
    "output_roles",
    "plan_recipe",
    "plan_section",
]

#: The only sections swage plans. `build` holds compilers with no relationship
#: to upstream metadata, and `run_constraints` is read, never authored.
PLANNED_SECTIONS = ("host", "run")


@dataclass(frozen=True)
class PlannedSection:
    """One requirements section as swage would write it."""

    path: str
    section: str
    requirements: tuple[PlannedRequirement, ...] = ()
    #: Every line the current upstream does not declare, with its fate. Kept
    #: even for lines that stay, because the report explains what was
    #: considered as well as what changed.
    removals: tuple[Removal, ...] = ()
    #: Lines swage could not account for. G1 reads this.
    unexplained: tuple[Unexplained, ...] = ()

    @property
    def dropped(self) -> tuple[Removal, ...]:
        return tuple(removal for removal in self.removals if removal.removed)


@dataclass(frozen=True)
class RecipePlan:
    """Everything swage intends to do to one recipe."""

    sections: tuple[PlannedSection, ...] = ()
    #: `run_constraints` entries no config association explains (G9).
    unassociated_constraints: tuple[UnassociatedConstraint, ...] = ()
    #: Recorded so a plan that changed because conda-forge moved the build
    #: floor is explainable after the fact (DESIGN.md 9.2).
    python_min: PythonMin | None = None
    #: Upstream extras no output draws on and no config entry accounts for.
    #: Reported always; gated only where the feedstock declares a `skip` list.
    unaccounted_extras: tuple[str, ...] = field(default=())

    @property
    def unexplained(self) -> tuple[Unexplained, ...]:
        return tuple(u for section in self.sections for u in section.unexplained)

    @property
    def dropped(self) -> tuple[Removal, ...]:
        return tuple(r for section in self.sections for r in section.dropped)


def plan_section(
    block: RequirementsBlock,
    upstream: UpstreamMetadata,
    config: FeedstockConfig,
    resolver: NameResolver,
    python_min: PythonMin,
    listed_extras: Sequence[str] = (),
    core: bool = True,
    previous: UpstreamMetadata | None = None,
) -> PlannedSection:
    """Plan one requirements section."""
    index = build_index(
        upstream,
        listed_extras,
        resolver,
        core=core,
        section=block.section,
        embedded_extras=config.embedded_extras,
    )
    added = config.add_requirements.get(block.section, ())

    planned: dict[str, PlannedRequirement] = {}
    for name, variants, provenance in _upstream_groups(
        upstream, listed_extras, resolver, block.section, core
    ):
        result = reconcile(name, variants, python_min, config.feedstock)
        if not result.considered:
            # Every declaration was gated below the build floor, so upstream
            # does not ask for this package on any Python conda-forge ships.
            continue
        text = f"{name} {result.specifier}" if result.specifier else name
        comments = (f"# {result.note}",) if result.note else ()
        planned[name] = PlannedRequirement(text, provenance, comments)
        for expansion, detail, source in _expansions(variants, config):
            expanded = parse_line(expansion)
            planned.setdefault(
                expanded.name,
                PlannedRequirement(
                    expanded.rendered,
                    Provenance("config-add", f"{detail} ({source})"),
                ),
            )

    for entry in added:
        planned.setdefault(
            parse_line(entry.text).name,
            PlannedRequirement(
                parse_line(entry.text).rendered,
                Provenance("config-add", entry.source),
            ),
        )

    removals: list[Removal] = []
    unexplained: list[Unexplained] = []
    previous_index = (
        build_index(previous, listed_extras, resolver, core=core, section=block.section)
        if previous is not None
        else None
    )

    for requirement in block.content.requirements:
        line = parse_line(requirement.text)
        explanation = attribute(line, index, config.recipe_owned, added)
        if isinstance(explanation, Unexplained):
            unexplained.append(explanation)

        if line.name in planned:
            # Upstream still asks for it; the reconciled line replaces this one.
            continue

        removal = classify_removal(
            line,
            index,
            config.recipe_owned,
            previous=previous_index,
            previous_known=previous is not None,
            version=upstream.version,
        )
        removals.append(removal)
        if removal.removed:
            continue
        # Kept: recipe-owned structure, or something swage will not delete.
        planned[line.name] = PlannedRequirement(
            line.rendered,
            explanation
            if isinstance(explanation, Provenance)
            else Provenance("recipe-kept", "kept, unexplained"),
            requirement.comments,
        )

    ordered = order_requirements(tuple(planned.values()), index.order)
    return PlannedSection(
        path=block.path,
        section=block.section,
        requirements=_with_extra_headers(ordered),
        removals=tuple(removals),
        unexplained=tuple(unexplained),
    )


def _upstream_groups(
    upstream: UpstreamMetadata,
    listed_extras: Sequence[str],
    resolver: NameResolver,
    section: str,
    core: bool,
) -> list[tuple[str, list[UpstreamRequirement], Provenance]]:
    """Group upstream's requirements by the conda name they resolve to.

    Core wins over an extra where a package appears in both, matching
    attribution's order (DESIGN.md 3.3.10) so a line's provenance does not
    depend on which code looked at it.
    """
    groups: dict[str, list[UpstreamRequirement]] = {}
    provenance: dict[str, Provenance] = {}

    def add(requirement: UpstreamRequirement, origin: Provenance) -> None:
        resolution = resolver.resolve(requirement.key) or resolver.resolve(
            requirement.name
        )
        name = resolution.conda_name if resolution else requirement.name
        groups.setdefault(name, []).append(requirement)
        provenance.setdefault(
            name,
            Provenance(origin.origin, origin.detail, resolution),
        )

    if core:
        source = (
            (upstream.build_requires or ())
            if section == "host"
            else upstream.dependencies
        )
        for requirement in source:
            add(requirement, Provenance("upstream-core", "upstream"))

    if section != "host":
        for extra in listed_extras:
            for requirement in upstream.optional_dependencies.get(extra, ()):
                add(requirement, Provenance("upstream-extra", f"extra:{extra}"))

    return [(name, variants, provenance[name]) for name, variants in groups.items()]


def _expansions(
    variants: Sequence[UpstreamRequirement], config: FeedstockConfig
) -> list[tuple[str, str, str]]:
    """The lines config says a dependency-carried extra stands in for."""
    found: list[tuple[str, str, str]] = []
    for requirement in variants:
        if not requirement.extras:
            continue
        entry = config.embedded_extras.lookup(requirement.key)
        if entry is None:
            continue
        lines, source = entry
        found.extend(
            (line, f"embedded_extras:{requirement.key}", source) for line in lines
        )
    return found


def _with_extra_headers(
    ordered: Sequence[PlannedRequirement],
) -> tuple[PlannedRequirement, ...]:
    """Introduce each extra's dependencies with a header naming it (DESIGN.md 6).

    A header runs until the next header or the end of the section, so one
    comment per *extra* rather than per dependency: `google-cloud-bigquery`
    folds in nine extras, and annotating each of its twenty-odd lines
    individually would bury the recipe in redundancy.
    """
    result: list[PlannedRequirement] = []
    current: str | None = None
    for entry in ordered:
        extra = (
            entry.provenance.detail.removeprefix("extra:")
            if entry.provenance.origin == "upstream-extra"
            else None
        )
        if extra is not None and extra != current:
            entry = PlannedRequirement(
                entry.text,
                entry.provenance,
                (f"# from the {extra} extra", *entry.comments),
            )
        current = extra
        result.append(entry)
    return tuple(result)


def output_roles(
    recipe: Recipe, config: FeedstockConfig
) -> dict[str, tuple[tuple[str, ...], bool]]:
    """What each output draws on: its extras, and whether it takes core deps.

    Two config shapes express this and a feedstock may use both (DESIGN.md 4).
    `outputs[].run` folds extras into an existing output -- the google-cloud
    shape. `extras_as_outputs` publishes each extra as an output of its own --
    the airflow shape -- and those outputs are metapackages: they carry the
    extra's dependencies and a `pin_subpackage` back to the real package, and
    take none of upstream's own dependencies. Handing them core would add every
    runtime dependency to a package that installs nothing.

    An output named by neither takes core and no extras, which is what a
    single-output feedstock wants.
    """
    roles: dict[str, tuple[tuple[str, ...], bool]] = {}

    extras_as_outputs = config.extras_as_outputs
    if extras_as_outputs is not None:
        for extra in extras_as_outputs.supported:
            name = extras_as_outputs.suffix.format(name=config.feedstock, extra=extra)
            roles[name] = ((extra,), False)

    for name, output in config.outputs.items():
        roles[name] = (tuple(output.run.extras), output.run.core)

    return roles


def plan_recipe(
    recipe: Recipe,
    upstream: UpstreamMetadata,
    config: FeedstockConfig,
    resolver: NameResolver,
    python_min: PythonMin,
    previous: UpstreamMetadata | None = None,
    outputs: Mapping[str, tuple[tuple[str, ...], bool]] | None = None,
) -> RecipePlan:
    """Plan every section of every output.

    ``outputs`` overrides what each output draws on; where it says nothing, the
    roles come from config via `output_roles`.
    """
    roles = dict(output_roles(recipe, config))
    roles.update(outputs or {})

    sections: list[PlannedSection] = []
    for output in recipe.outputs:
        listed, core = roles.get(output.name or "", ((), True))
        for name in PLANNED_SECTIONS:
            block = output.blocks.get(name)
            if block is None:
                continue
            sections.append(
                plan_section(
                    block,
                    upstream,
                    config,
                    resolver,
                    python_min,
                    listed_extras=listed,
                    core=core,
                    previous=previous,
                )
            )

    constrained = [
        text
        for output in recipe.outputs
        for block in [output.blocks.get("run_constraints")]
        if block is not None
        for text in block.content.texts()
    ]

    drawn = {extra for listed, _ in (outputs or {}).values() for extra in listed}
    return RecipePlan(
        sections=tuple(sections),
        unassociated_constraints=check_run_constraints(
            constrained, config.run_constraints
        ),
        python_min=python_min,
        unaccounted_extras=tuple(
            extra for extra in upstream.extras if extra not in drawn
        ),
    )
