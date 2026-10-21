"""The plan: what swage would do to one recipe, and what it found (DESIGN.md
§9.8).

`plan_recipe` is the `plan` stage of the pipeline (§12.2): every section of
every output through `plan_section`, then the checks, then the rendering.
`unchanged` is byte identity of the whole file (v1 §5.3).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from swage.config import AddedRequirement, FeedstockConfig
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
    AppliedOverride,
    PlannedSection,
    SelfConflict,
    accounted_extras,
    cross_compiled_hosts,
    mirrored_sections,
    plan_section,
    self_conflicts,
)
from .attribute import Unexplained
from .constrained import ExtraConstraint, transcribed_extras
from .entry_points import EntryPointChange, plan_entry_points
from .findings import Finding, find
from .model import PlannedEntry, PlannedRequirement
from .output import Output, derive_outputs
from .own_extras import expand_own_extras
from .python_min import (
    DroppedFloor,
    FloorChange,
    PythonMin,
    reconcile_python_min,
)
from .removals import Removal
from .skip_floor import RaisedSkip, UnskippedPythons, reconcile_skip_floors
from .test_matrix import TestMatrix, plan_test_matrices

__all__ = [
    "Plan",
    "SourceCorrection",
    "plan_recipe",
    "planned_blocks",
    "planned_entry_points",
    "planned_matrices",
]


@dataclass(frozen=True)
class SourceCorrection:
    """A second source's version, moved before planning (v1 §3.6.5): the
    recipe as it was read, and what moved, one line each.
    """

    read: str
    moved: tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    """Everything swage intends to do to one recipe, and what it found.

    The recipe and the release travel with it because every reader needs them
    beside it (DESIGN.md §9.8, §16).
    """

    #: The recipe planned against: the pull request's, or the conversion of a
    #: v0 recipe, or one whose source versions swage corrected first.
    recipe: Recipe
    upstream: RecipeUpstream
    sections: tuple[PlannedSection, ...] = ()
    #: The build model each section was planned under, one per recipe output
    #: (DESIGN.md §9.1).
    outputs: tuple[Output, ...] = ()
    #: `run_constraints` entries that transcribe an upstream extra, which
    #: config has not recorded as looked at (DESIGN.md §9.7).
    extra_constraints: tuple[ExtraConstraint, ...] = ()
    #: Recorded so a plan that changed because conda-forge moved the build
    #: floor is explainable after the fact (design-v1.md 9.2).
    python_min: PythonMin | None = None
    #: What changes about `context.python_min`, which `python_min` above
    #: already reflects (DESIGN.md §16).
    floor_change: FloorChange | None = None
    #: The `build.skip` conditions raised to upstream's python floor, one per
    #: condition, which `outputs` already reflects (DESIGN.md §16).
    raised_skips: tuple[RaisedSkip, ...] = ()
    #: Arch outputs built for a python upstream excludes, whose `build.skip`
    #: has no floor to raise.
    unskipped: tuple[UnskippedPythons, ...] = ()
    #: Upstream extras no output draws on and no config entry accounts for.
    #: Reported always; gated only where the feedstock declares a `skip` list.
    unaccounted_extras: tuple[str, ...] = field(default=())
    #: Python test matrices swage would complete (DESIGN.md §9.6).
    test_matrices: tuple[TestMatrix, ...] = field(default=())
    #: The outputs that cross-compile and whose `host` section swage would
    #: change. G13 reads this (design-v1.md 3.3.6.1).
    cross_compiled: tuple[str, ...] = field(default=())
    #: Requirements on a package this same recipe builds, at a version this
    #: recipe does not build. G14 reads this (design-v1.md 3.6).
    self_conflicts: tuple[SelfConflict, ...] = field(default=())
    #: `build.python.entry_points` lists swage would rewrite to say what
    #: upstream declares (DESIGN.md §9.6). G15 reads the lines it would drop.
    entry_points: tuple[EntryPointChange, ...] = field(default=())
    #: What swage looked at and left alone, said beside the verdict: a list
    #: holding an `if:` entry. Never gated.
    entry_point_notes: tuple[str, ...] = field(default=())
    #: What the checks found against everything above, in the table's order
    #: (DESIGN.md §9.7).
    findings: tuple[Finding, ...] = ()
    #: The recipe exactly as swage would push it.
    rendered: str = ""
    #: Set where `recipe` is one whose source versions swage corrected before
    #: planning: the correction is part of the change (v1 §3.6.5).
    correction: SourceCorrection | None = None

    @property
    def read(self) -> str:
        """The recipe as it was read, which the change is measured against."""
        return self.correction.read if self.correction is not None else self.recipe.text

    @property
    def unchanged(self) -> bool:
        """Whether swage would leave the recipe alone: byte identity (v1 §5.3)."""
        return self.rendered == self.read

    @property
    def unexplained(self) -> tuple[Unexplained, ...]:
        return tuple(u for section in self.sections for u in section.unexplained)

    @property
    def dropped(self) -> tuple[Removal, ...]:
        return tuple(r for section in self.sections for r in section.dropped)

    @property
    def inferred_removals(self) -> tuple[Removal, ...]:
        """The removals swage inferred, which are the ones G8 gates.

        `dropped` is every line swage will remove; this is the subset resting
        on swage's own reading rather than on a `retire` entry (DESIGN.md
        §9.5).
        """
        return tuple(
            removal
            for removal in self.dropped
            if removal.fate in {"upstream-dropped", "out-of-range"}
        )

    @property
    def overrides(self) -> tuple[AppliedOverride, ...]:
        return tuple(o for section in self.sections for o in section.overrides)

    @property
    def overruled(self) -> tuple[AppliedOverride, ...]:
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
    conda_forge_floor: PythonMin | None = None,
    pythons: Sequence[int] = (),
    platforms: Sequence[str] = (),
    pinned: frozenset[str] = frozenset(),
) -> Plan:
    """Plan every section of every output.

    ``python_min``, ``pythons``, ``platforms`` and ``pinned`` are what the
    recipe and `.ci_support` say about how the feedstock is built, and
    ``conda_forge_floor`` what `.ci_support` alone says of the floor;
    `derive_outputs` turns them into one `Output` each (DESIGN.md §9.1).
    ``upstream`` is a set of releases, one per archive a recipe builds
    (v1 §3.6).
    """
    # Before the outputs, whose universe starts at the floor (DESIGN.md §16).
    change = None
    if python_min is not None:
        python_min, change = reconcile_python_min(
            recipe, upstream, python_min, conda_forge_floor
        )
    raised_skips, unskipped = reconcile_skip_floors(recipe, upstream, pythons)
    outputs = _skipped(
        derive_outputs(recipe, config, python_min, pythons, platforms, pinned),
        raised_skips,
    )

    # Once, before anything reads an extra (v1 §3.3.12).
    upstream = upstream.map(lambda release: expand_own_extras(release, resolver))
    if previous is not None:
        previous = previous.map(lambda release: expand_own_extras(release, resolver))

    sections: list[PlannedSection] = []
    for output in outputs:
        release = upstream.for_output(output.package or "")
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
    # for is what the outputs draw.
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
        extra_constraints=transcribed_extras(
            constrained, config.run_constraints, upstream, resolver
        ),
        python_min=python_min,
        floor_change=change,
        raised_skips=raised_skips,
        unskipped=unskipped,
        unaccounted_extras=tuple(
            extra for extra in upstream.extras if extra not in accounted
        ),
        # Reads the recipe and conda-forge convention, not upstream metadata, so
        # it is one call rather than a per-section concern.
        test_matrices=plan_test_matrices(recipe),
        entry_points=entry_points,
        entry_point_notes=entry_point_notes,
    )
    # Found and rendered here rather than by each command (DESIGN.md §9.8).
    # Every kind of edit goes into the rendering, or the byte comparison would
    # call a changed recipe unchanged.
    findings = find(assembled, config, upstream)
    return replace(
        assembled,
        findings=findings,
        rendered=render_recipe(
            recipe,
            planned_blocks(assembled),
            planned_matrices(assembled),
            planned_entry_points(assembled),
            *planned_python_min(assembled),
            skips=planned_skips(assembled),
        ),
    )


def _skipped(
    outputs: tuple[Output, ...], raised: Sequence[RaisedSkip]
) -> tuple[Output, ...]:
    """Each output with a raised skip built only from its new floor up, so
    no marker is read on a python it no longer builds (DESIGN.md §16).
    """
    floors = {skip.position: skip.minor for skip in raised}
    return tuple(
        output
        if position not in floors
        else replace(
            output,
            pythons=tuple(
                minor for minor in output.pythons if minor >= floors[position]
            ),
        )
        for position, output in enumerate(outputs)
    )


def planned_skips(plan: Plan) -> dict[str, str]:
    """The plan as the writer takes it: skip condition path -> its new text."""
    return {skip.path: skip.text for skip in plan.raised_skips}


def planned_python_min(plan: Plan) -> tuple[str | None, bool]:
    """The plan as the writer takes it: `context.python_min`'s new value, and
    whether the entry goes.
    """
    change = plan.floor_change
    if isinstance(change, DroppedFloor):
        return None, True
    return (change.to if change is not None else None), False


def planned_entry_points(plan: Plan) -> dict[str, tuple[str, ...]]:
    """The plan as the writer takes it: list path -> the items it should hold."""
    return {change.path: change.items for change in plan.entry_points}


def planned_matrices(plan: Plan) -> dict[str, tuple[str, ...]]:
    """The plan as the writer takes it: test path -> the versions it should
    test.
    """
    return {matrix.path: matrix.versions for matrix in plan.test_matrices}


def planned_blocks(plan: Plan) -> dict[str, BlockContent]:
    """The plan as the writer takes it: block path -> that section's new body.

    Built in one place, because this is where a plan becomes bytes and
    `unchanged` rests on those bytes (v1 §5.3).
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

    Comments land on the first entry, which is where they render (v1 §6.1).
    """
    if isinstance(entry, PlannedRequirement):
        return (Requirement(entry.text, entry.comments),)
    first, *rest = entry.conditionals
    return (replace(first, comments=entry.comments), *rest)
