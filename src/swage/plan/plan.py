"""The plan: what swage would do to one recipe, and what it found (DESIGN.md §9.8).

`plan_recipe` is the `plan` stage of the pipeline (DESIGN.md §12.2). It
assembles every section of every output through `plan_section`, asks the
checks what they make of the result, and renders the recipe as swage would
write it -- so that one value answers the three questions every command
puts to it: what changes, what was found, and whether anything changes at
all. `unchanged` is byte identity of the whole file, because swage owns the
comments inside a requirements block as much as the dependencies, and "no
modification needed" is byte identity or it is nothing (v1 §5.3).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from swage.config import AddedRequirement, FeedstockConfig, Override
from swage.mapping import NameResolver
from swage.recipe import (
    BlockContent,
    Entry,
    Recipe,
    Requirement,
    render_recipe,
)
from swage.upstream import RecipeUpstream

from .assemble import (
    PlannedSection,
    SelfConflict,
    accounted_extras,
    cross_compiled_hosts,
    mirrored_sections,
    plan_section,
    self_conflicts,
)
from .attribute import Unexplained
from .constrained import UnassociatedConstraint, check_run_constraints
from .entry_points import EntryPointChange, plan_entry_points
from .findings import Finding, find
from .model import PlannedEntry, PlannedRequirement
from .output import Output, derive_outputs
from .python_min import PythonMin, check_upstream_floor
from .removals import Removal
from .test_matrix import TestMatrix, plan_test_matrices

__all__ = [
    "Plan",
    "plan_recipe",
    "planned_blocks",
    "planned_entry_points",
    "planned_matrices",
]


@dataclass(frozen=True)
class Plan:
    """Everything swage intends to do to one recipe, and what it found.

    The recipe and the release it was planned against travel with it: the
    record quotes the recipe's lines beside the plan's, `unchanged` is a
    comparison with the recipe's text, and the release's name is what the
    commit message and the pull request comment say. Every command needs all
    three beside the plan, which is why they are in it (DESIGN.md §9.8).
    """

    #: The recipe planned against: the pull request's, or the conversion of a
    #: v0 recipe, or one whose source versions swage corrected first.
    recipe: Recipe
    upstream: RecipeUpstream
    sections: tuple[PlannedSection, ...] = ()
    #: The build model each section was planned under, one per recipe output
    #: (DESIGN.md §9.1). The record carries it so that `swage explain` can say
    #: which pythons and how many artifacts a marker was read against.
    outputs: tuple[Output, ...] = ()
    #: `run_constraints` entries no config association explains (G9).
    unassociated_constraints: tuple[UnassociatedConstraint, ...] = ()
    #: Recorded so a plan that changed because conda-forge moved the build
    #: floor is explainable after the fact (design-v1.md 9.2).
    python_min: PythonMin | None = None
    #: Upstream extras no output draws on and no config entry accounts for.
    #: Reported always; gated only where the feedstock declares a `skip` list.
    unaccounted_extras: tuple[str, ...] = field(default=())
    #: Python test matrices swage would complete (design-v1.md 3.7). The one part
    #: of a plan that is not about requirements, and the reason "only
    #: requirements changed" is now checked rather than structural.
    test_matrices: tuple[TestMatrix, ...] = field(default=())
    #: `host` sections swage would change on an output that cross-compiles,
    #: each named the way its message says it. G13 reads this
    #: (design-v1.md 3.3.6.1).
    cross_compiled: tuple[str, ...] = field(default=())
    #: Requirements on a package this same recipe builds, at a version this
    #: recipe does not build. G14 reads this (design-v1.md 3.6).
    self_conflicts: tuple[SelfConflict, ...] = field(default=())
    #: `build.python.entry_points` lists swage would rewrite to say what
    #: upstream declares (design-v1.md 3.3.15). The second part of a plan that
    #: is not about requirements; G15 reads the lines it would drop.
    entry_points: tuple[EntryPointChange, ...] = field(default=())
    #: What swage looked at and left alone, said beside the verdict: a list
    #: holding an `if:` entry. Never gated.
    entry_point_notes: tuple[str, ...] = field(default=())
    #: What the checks found against everything above, in the table's order
    #: (DESIGN.md §9.7).
    findings: tuple[Finding, ...] = ()
    #: The recipe exactly as swage would push it.
    rendered: str = ""

    @property
    def unchanged(self) -> bool:
        """Whether swage would leave the recipe alone: byte identity (v1 §5.3)."""
        return self.rendered == self.recipe.text

    @property
    def unexplained(self) -> tuple[Unexplained, ...]:
        return tuple(u for section in self.sections for u in section.unexplained)

    @property
    def dropped(self) -> tuple[Removal, ...]:
        return tuple(r for section in self.sections for r in section.dropped)

    @property
    def inferred_removals(self) -> tuple[Removal, ...]:
        """The removals swage *inferred*, which are the ones G8 gates.

        `dropped` is every line swage will actually remove, and is what the
        report renders. This is the subset G8 asks about, and the difference is
        where the justification came from (design-v1.md 3.3.8).

        An `upstream-dropped` removal rests on swage's own reading of two
        releases, and an `out-of-range` one on its reading of a marker against
        the build floor -- claims with no track record behind them, whose
        failure mode is silent. A `retired` one rests on a hand-written
        `retire` entry, and is only ever reached once upstream has been asked
        and had nothing to say about that name in any version or under any
        extra. Config has already answered it, so holding it for review asks a
        maintainer to re-decide something they wrote down, on every feedstock
        the entry covers, every time. `assemble` exempts a retired line from G1
        for exactly this reason; G8 was simply never given the same treatment.
        """
        return tuple(
            removal
            for removal in self.dropped
            if removal.fate in {"upstream-dropped", "out-of-range"}
        )

    @property
    def overrides(self) -> tuple[Override, ...]:
        return tuple(o for section in self.sections for o in section.overrides)

    @property
    def overruled(self) -> tuple[Override, ...]:
        return tuple(o for section in self.sections for o in section.overruled)

    @property
    def temporary_additions(self) -> tuple[AddedRequirement, ...]:
        return tuple(
            addition
            for section in self.sections
            for addition in section.temporary_additions
        )


def plan_recipe(
    recipe: Recipe,
    upstream: RecipeUpstream,
    config: FeedstockConfig,
    resolver: NameResolver,
    python_min: PythonMin | None,
    previous: RecipeUpstream | None = None,
    pythons: Sequence[int] = (),
    platforms: Sequence[str] = (),
    pinned: frozenset[str] = frozenset(),
) -> Plan:
    """Plan every section of every output.

    ``python_min``, ``pythons``, ``platforms`` and ``pinned`` are what the
    recipe and `.ci_support` say about how the feedstock is built, and
    `derive_outputs` turns them into one `Output` per recipe output (DESIGN.md
    §9.1). The demand for a floor is made per output, because that is the only
    place it is known whether one was needed (design-v1.md 3.3.3).

    ``upstream`` is a set of releases rather than one, because a recipe may
    build several and an output reconciles against its own (design-v1.md 3.6).
    For the recipes that build one -- all but four of the fleet -- every
    output is handed the same release and nothing below can tell the
    difference.
    """
    outputs = derive_outputs(recipe, config, python_min, pythons, platforms, pinned)

    sections: list[PlannedSection] = []
    for recipe_output, output in zip(recipe.outputs, outputs, strict=True):
        release = upstream.for_output(output.package or "")
        if output.noarch and python_min is not None:
            # Both numbers are in hand exactly here, which is why the check
            # lives here rather than in config (design-v1.md 4.1).
            check_upstream_floor(recipe_output, release.requires_python, python_min)
        for block in output.sections:
            sections.append(
                plan_section(
                    block,
                    release,
                    config,
                    resolver,
                    output,
                    previous=(
                        None
                        if previous is None
                        else previous.for_output(output.package or "")
                    ),
                    context=recipe.context,
                )
            )

    constrained = [
        text
        for output in recipe.outputs
        for block in [output.blocks.get("run_constraints")]
        if block is not None
        for text in block.content.texts()
    ]

    # Every extra an output draws is named in config, so what config accounts
    # for is what the outputs draw -- including the nine `google-cloud-bigquery`
    # folds into its metapackage.
    accounted = accounted_extras(config)
    mirrored, in_step = mirrored_sections(recipe, outputs, sections)
    # Reconciled unless the feedstock says its list is conda-forge's own; a
    # `manual` list is not looked at, so not even the note is written.
    entry_points, entry_point_notes = (
        plan_entry_points(recipe, upstream)
        if config.entry_points == "reconcile"
        else ((), ())
    )
    assembled = Plan(
        recipe,
        upstream,
        sections=(*sections, *mirrored),
        outputs=outputs,
        cross_compiled=cross_compiled_hosts(recipe, outputs, sections, config, in_step),
        self_conflicts=self_conflicts(recipe, upstream, sections),
        unassociated_constraints=check_run_constraints(
            constrained, config.run_constraints
        ),
        python_min=python_min,
        unaccounted_extras=tuple(
            extra for extra in upstream.extras if extra not in accounted
        ),
        # Independent of everything above: it reads the recipe and conda-forge
        # convention, not upstream metadata, which is why it is one call rather
        # than a per-section concern.
        test_matrices=plan_test_matrices(recipe),
        entry_points=entry_points,
        entry_point_notes=entry_point_notes,
    )
    # Found and rendered here rather than by each command, so a plan cannot
    # be pushed without its findings or judged unchanged without its bytes.
    # Every kind of edit goes into the rendering, or the byte comparison
    # would call a recipe swage is about to change unchanged (v1 §3.7).
    findings = find(assembled, config, upstream)
    return replace(
        assembled,
        findings=findings,
        rendered=render_recipe(
            recipe,
            planned_blocks(assembled),
            planned_matrices(assembled),
            planned_entry_points(assembled),
        ),
    )


def planned_entry_points(plan: Plan) -> dict[str, tuple[str, ...]]:
    """The plan as the writer takes it: list path -> the items it should hold.

    The third kind of edit, and the same rule as the two before it: the byte
    comparison has to see every kind or a changed recipe reads as unchanged.
    """
    return {change.path: change.items for change in plan.entry_points}


def planned_matrices(plan: Plan) -> dict[str, tuple[str, ...]]:
    """The plan as the writer takes it: test path -> the versions it should test.

    The companion to `planned_blocks`, and here for the same reason: this is
    where a plan becomes bytes, and the byte comparison that decides whether
    swage would change anything has to see both kinds of edit or it will call a
    changed recipe unchanged.
    """
    return {matrix.path: matrix.versions for matrix in plan.test_matrices}


def planned_blocks(plan: Plan) -> dict[str, BlockContent]:
    """The plan as the writer takes it: block path -> that section's new body.

    Here rather than at each call site because it is the one place a plan
    becomes bytes, and that is the comparison G7 rests on (design-v1.md 5.3): a
    section swage would render differently is a section it would rewrite. Two
    callers building this separately is two chances for one of them to forget
    the trailing comments and quietly turn "no changes needed" into "changed".
    """
    return {
        section.path: BlockContent(
            tuple(written for entry in section.entries for written in _written(entry)),
            section.trailing_comments,
        )
        for section in plan.sections
    }


def _written(entry: PlannedEntry) -> tuple[Entry, ...]:
    """One planned entry as the entries the recipe model holds.

    A dependency stated per python range is several entries and one plan entry,
    so this is where the two views meet. Its comments travel beside its
    conditionals rather than on them, so that every planned entry answers
    `comments` the same way and design-v1.md 6.1 needs only one spelling; they
    land on the first entry, which is where they render.
    """
    if isinstance(entry, PlannedRequirement):
        return (Requirement(entry.text, entry.comments),)
    first, *rest = entry.conditionals
    return (replace(first, comments=entry.comments), *rest)
