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

from collections.abc import Container, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from swage.config import (
    AddedRequirement,
    FeedstockConfig,
    Layered,
    NotPackaged,
    Override,
    VariantCondition,
)
from swage.mapping import NameResolver, normalize_name
from swage.recipe import (
    BlockContent,
    Conditional,
    Entry,
    Recipe,
    Requirement,
    RequirementsBlock,
    inline_text,
    resolve_expression,
)
from swage.upstream import RecipeUpstream, UpstreamMetadata, UpstreamRequirement

from .attribute import (
    KEPT_UNEXPLAINED,
    Attribution,
    AttributionIndex,
    Provenance,
    Unexplained,
    attribute,
    build_index,
    drawn_on,
)
from .authored import maintainer_comments
from .constrained import UnassociatedConstraint, check_run_constraints
from .errors import PlanError
from .lines import ParsedLine, parse_line, spec_key
from .model import PlannedConditional, PlannedEntry, PlannedRequirement
from .order import order_requirements
from .prose import output_phrase, section_phrase
from .python_min import PythonMin, check_upstream_floor, python_ceiling
from .reconcile import reconcile, settled_already
from .removals import Removal, classify_removal
from .resolve import resolve_requirement
from .split import Split, split_by_environment, split_by_platform
from .test_matrix import TestMatrix, plan_test_matrices

__all__ = [
    "PlannedSection",
    "RecipePlan",
    "output_roles",
    "output_selections",
    "plan_recipe",
    "plan_section",
    "planned_blocks",
]

#: The only sections swage plans today. `run_constraints` is read, never
#: authored (DESIGN.md 3.3.9). `build` is a longer story: most of it is
#: compilers and cross-compilation helpers that answer no question upstream
#: metadata asks, but a cross-compilation block also repeats `host`'s
#: upstream-derived entries. Whether a requirement belongs in one is open
#: (DESIGN.md 3.3.6.1); keeping a copy that is already there in step with the
#: line it copies is not, and `_mirrors` does that without planning the
#: section.
PLANNED_SECTIONS = ("host", "run")


@dataclass(frozen=True)
class SelfConflict:
    """A requirement on a package this recipe builds, at a version it does not.

    Only a split recipe can produce one, and `airflow` did on its first run.
    Its `context.task_sdk_version` pins the `apache-airflow-task-sdk` sdist at
    1.3.0 -- so that is the version the recipe builds -- while
    `apache-airflow-core` 3.3.1 requires `apache-airflow-task-sdk==1.3.1`. The
    bot bumped `version` and left `task_sdk_version` alone, which is what the
    line beside it says to do by hand.

    swage reconciles the requirement correctly; the cause is in `context`, and
    whether swage may write there is `source_versions` (DESIGN.md 3.6.5). Where
    it may, the entry has already been corrected by the time planning starts
    and this reports what could not be corrected -- an ambiguous pin, an entry
    swage could not identify. Where it may not, this is the whole answer and a
    person makes the edit.
    """

    #: The output whose `run` states it.
    output: str
    #: The package, as the recipe names it.
    package: str
    #: The constraint swage would write.
    constraint: str
    #: The version this recipe builds that package at.
    built: str


@dataclass(frozen=True)
class PlannedSection:
    """One requirements section as swage would write it."""

    #: Where the block is in the parsed document. A key rather than prose:
    #: the writer splices by it and the run artifact records it, and no
    #: report prints it -- `where` is what a person reads (DESIGN.md 9.2).
    path: str
    section: str
    #: The same section, said the way the recipe's maintainer would say it.
    where: str = ""
    entries: tuple[PlannedEntry, ...] = ()
    #: Every line the current upstream does not declare, with its fate. Kept
    #: even for lines that stay, because the report explains what was
    #: considered as well as what changed.
    removals: tuple[Removal, ...] = ()
    #: Lines swage could not account for. G1 reads this.
    unexplained: tuple[Unexplained, ...] = ()
    #: Temporary overrides this section applied. G11 reads this, so a
    #: workaround is re-checked at every update rather than becoming permanent
    #: by nobody looking (DESIGN.md 3.3.14).
    overrides: tuple[Override, ...] = ()
    #: Overruling bounds this section applied -- one per line where upstream's
    #: own declarations intersected to nothing and config said which of them
    #: this package states (DESIGN.md 3.3.2). G11 reads this too: overruling
    #: upstream is provisional, so the entry is re-asked about at every update.
    #:
    #: Separate from `overrides` because the question differs. A temporary
    #: constraint asks whether the workaround is still needed; this asks
    #: whether upstream still disagrees with itself, and what it disagrees
    #: about now.
    overruled: tuple[Override, ...] = ()
    #: Temporary `add_requirements` entries this section actually carries. G11
    #: reads this beside `overrides`, for the same reason and about the other
    #: half of the same question: a conda-forge-only line held for now rather
    #: than for good (DESIGN.md 3.3.14, 4).
    #:
    #: The ones it *carries*, not the ones config declares. An entry whose
    #: dependency the recipe states inside a condition explains that line and
    #: adds nothing, so asking about it would be asking about a line swage did
    #: not write.
    temporary_additions: tuple[AddedRequirement, ...] = ()
    #: Comments after the last requirement and still inside the block. The
    #: `# end` half of an embedded-extras marker pair lands here when the
    #: expansion runs to the end of the section, which is the common case and
    #: has no following requirement to sit above (DESIGN.md 6).
    trailing_comments: tuple[str, ...] = ()

    @property
    def requirements(self) -> tuple[PlannedRequirement, ...]:
        """The unconditional entries, in order.

        **Not everything the section holds**, the same distinction and the same
        name as `BlockContent.requirements`: an output built once per python
        states a python-gated dependency as a condition, and a caller that
        wants one line per dependency has nothing to say about that. Anything
        deciding what swage will *write* -- the renderer, the gates, the report
        -- reads `entries` instead.
        """
        return tuple(
            entry for entry in self.entries if isinstance(entry, PlannedRequirement)
        )

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
    #: Python test matrices swage would complete (DESIGN.md 3.7). The one part
    #: of a plan that is not about requirements, and the reason "only
    #: requirements changed" is now checked rather than structural.
    test_matrices: tuple[TestMatrix, ...] = field(default=())
    #: `host` sections swage would change on an output that cross-compiles,
    #: each named the way its message says it. G13 reads this
    #: (DESIGN.md 3.3.6.1).
    cross_compiled: tuple[str, ...] = field(default=())
    #: Requirements on a package this same recipe builds, at a version this
    #: recipe does not build. G14 reads this (DESIGN.md 3.6).
    self_conflicts: tuple[SelfConflict, ...] = field(default=())

    @property
    def unexplained(self) -> tuple[Unexplained, ...]:
        return tuple(u for section in self.sections for u in section.unexplained)

    @property
    def dropped(self) -> tuple[Removal, ...]:
        return tuple(r for section in self.sections for r in section.dropped)

    @property
    def upstream_dropped(self) -> tuple[Removal, ...]:
        """The removals swage *inferred*, which are the ones G8 gates.

        `dropped` is every line swage will actually remove, and is what the
        report renders. This is the subset G8 asks about, and the difference is
        where the justification came from (DESIGN.md 3.3.8).

        An `upstream-dropped` removal rests on swage's own reading of two
        releases -- a claim with no track record behind it, whose failure mode
        is silent. A `retired` one rests on a hand-written `retire` entry, and
        is only ever reached once upstream has been asked and had nothing to
        say about that name in any version or under any extra. Config has
        already answered it, so holding it for review asks a maintainer to
        re-decide something they wrote down, on every feedstock the entry
        covers, every time. `assemble` exempts a retired line from G1 for
        exactly this reason; G8 was simply never given the same treatment.
        """
        return tuple(
            removal for removal in self.dropped if removal.fate == "upstream-dropped"
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


def _build_floor(
    block: RequirementsBlock, python_min: PythonMin | None, label: str = ""
) -> PythonMin:
    """The floor a noarch output collapses its markers over, or the stop.

    Demanded here rather than where it was resolved, because this is where it
    is known that an output needed one (DESIGN.md 3.3.3) -- and demanded
    whether or not upstream declares any dependency this version, so that a
    feedstock conda-smithy has never rendered says so at every version rather
    than only at the ones with a marker in them.
    """
    if python_min is not None:
        return python_min
    where = output_phrase(label)
    raise PlanError(
        f"cannot determine the python floor {where} is built from\n"
        "  it builds one noarch package installed on every python from that "
        "floor up, so the floor is both what ${{ python_min }} expands to and "
        "the bottom of the range upstream's python markers are read over\n"
        "  the recipe sets no context.python_min and no .ci_support file "
        "declares one -- run conda-smithy on this feedstock, or set "
        "context.python_min in the recipe"
    )


def plan_section(
    block: RequirementsBlock,
    upstream: UpstreamMetadata,
    config: FeedstockConfig,
    resolver: NameResolver,
    python_min: PythonMin | None,
    listed_extras: Sequence[str] = (),
    core: bool = True,
    output: str = "",
    label: str = "",
    from_extras: Mapping[str, frozenset[str]] | None = None,
    previous: UpstreamMetadata | None = None,
    python_max: Version | None = None,
    noarch: bool = True,
    pythons: Sequence[int] = (),
    platforms: Sequence[str] = (),
    pinned: Container[str] = frozenset(),
    context: Mapping[str, str] = MappingProxyType({}),
) -> PlannedSection:
    """Plan one requirements section.

    ``noarch`` is the build model of the output this section belongs to, and it
    decides what an upstream environment marker becomes. One noarch package is
    installed on every python from the build floor up, so several
    marker-qualified declarations collapse into the tightest bound that holds
    across the range. An architecture-specific output is built once per python,
    so they become conditions saying what upstream says (DESIGN.md 3.3.1.1).

    ``output`` is the name of the output this section belongs to, and is what
    an `add_requirements` entry naming one output is matched against. Empty for
    a recipe with no `outputs` list, which is also what such an entry can never
    name (DESIGN.md 4).

    ``label`` is what a report calls that output, which is the same string
    almost always and is not the same question: an output that only stages
    -- `gdal`'s `core-build` -- has requirements to report on and no package
    to match config against. It falls back to ``output``.

    ``platforms`` splits the first of those in two. Where conda-smithy renders
    more than one platform for a noarch output, the package is built once per
    platform, so the collapse happens once per artifact and a marker naming the
    platform becomes a condition -- the python axis behaving exactly as it does
    for a single artifact either way.
    """
    if noarch:
        _build_floor(block, python_min, label)
    index = build_index(
        upstream,
        listed_extras,
        resolver,
        # `core` says whether this output draws upstream's *runtime*
        # dependencies -- it is `outputs[].run.core` in config, nested under
        # `run` (DESIGN.md 4). Applying it to `host` as well left every output
        # with `core: false` unable to attribute the build backend upstream
        # declares, so `google-cloud-bigquery`'s metapackage reported its own
        # `setuptools` as coming from no upstream version.
        #
        # Attribution only: `_upstream_groups` below still honors `core`, so
        # an output whose `host` carries no backend does not acquire one.
        # Those two answers differ on purpose -- explaining a line that is
        # there and adding one that is not are different acts, and the fleet's
        # recipes disagree about which outputs build from source
        # (DESIGN.md 3.6.2).
        core=core or block.section == "host",
        section=block.section,
        output=label or output,
        embedded_extras=config.embedded_extras,
        from_extras=from_extras,
    )
    added = config.add_requirements.get(block.section, output) + _implicit_backend(
        block.section, upstream, config, core
    )

    planned: dict[str, PlannedEntry] = {}
    applied: list[Override] = []
    settled: list[Override] = []
    settled_names: set[str] = set()
    for name, variants, provenance in _upstream_groups(
        upstream,
        listed_extras,
        resolver,
        block.section,
        core,
        config.embedded_extras,
        from_extras,
    ):
        absent = _not_packaged(name, variants, config)
        if absent is not None:
            if provenance.mapping is not None:
                # The entry is the only thing keeping this dependency out of
                # the recipe, so it cannot outlive conda-forge packaging the
                # thing (DESIGN.md 3.2.3).
                raise PlanError(
                    _now_packaged(name, provenance.mapping.conda_name, config)
                )
            continue
        # Permanent and temporary overrides render identically -- the whole
        # difference is that a temporary one is asked about again at the next
        # update, which G11 does with what `applied` collects.
        override = config.constraints.get(name) or config.temporary_constraints.get(
            name
        )
        constraint = override.bound if override is not None else None
        if name in config.temporary_constraints:
            applied.append(config.temporary_constraints[name])
        # Not folded into `override` above: this one stands in for upstream's
        # declarations rather than narrowing them, and the schema keeps a name
        # out of more than one of the three keys (DESIGN.md 3.3.2).
        overruled = config.overruled_constraints.get(name)
        # Config's record that upstream's platform and machine markers for this
        # dependency are about its own wheel matrix (DESIGN.md 3.3.4.1). Every
        # path asks: `sqlalchemy` reads one declaration of `greenlet` into both
        # a compiled output and eight noarch ones, and an entry that reached
        # only the paths that stop would narrow the one that did not.
        built_everywhere = name in config.built_everywhere
        per_platform = noarch and len(platforms) > 1
        note: str | None = None
        if per_platform:
            split, note = split_by_platform(
                name,
                variants,
                _build_floor(block, python_min, label),
                platforms,
                config.feedstock,
                python_max,
                constraint=constraint,
                built_everywhere=built_everywhere,
                overruled=None if overruled is None else overruled.bound,
                output=label or output,
            )
            considered: Sequence[UpstreamRequirement] = split.considered
            if split.overruled and overruled is not None:
                settled.append(overruled)
                settled_names.add(name)
        elif noarch:
            result = reconcile(
                name,
                variants,
                _build_floor(block, python_min, label),
                config.feedstock,
                python_max,
                constraint=constraint,
                built_everywhere=built_everywhere,
                overruled=None if overruled is None else overruled.bound,
                output=label or output,
            )
            note = result.note
            considered = result.considered
            if result.overruled and overruled is not None:
                settled.append(overruled)
                settled_names.add(name)
        else:
            split = split_by_environment(
                name,
                variants,
                constraint=constraint,
                pythons=pythons,
                feedstock=config.feedstock,
                built_everywhere=built_everywhere,
            )
            considered = split.considered
        if not considered:
            # Every declaration is gated on a python this output does not
            # build, so upstream does not ask for this package here at all.
            # Dropping it is a removal decision the planner makes below, not
            # one to make here.
            continue
        if overruled is not None and overruled not in settled:
            # Upstream can stop contradicting itself, and then the entry is
            # deciding a line nobody is being asked about any more. Checked
            # here rather than inside `reconcile` because `split_by_platform`
            # asks it once per platform, and only one of them needing the
            # entry is enough (DESIGN.md 3.3.2).
            raise PlanError(settled_already(name, config.feedstock))
        # The noarch note names the marker behind the binding bound, because a
        # single artifact had to pick one and the reader is owed why. Nothing
        # was picked on the other path, so nothing is said (DESIGN.md 3.3.1.1).
        comments = _settled_captions(variants, config)
        as_written = _unversioned_reader_line(block, upstream, name, variants)
        if as_written is not None:
            # A build system says which packages, not which versions, so
            # there is no upstream bound here for the recipe's to disagree
            # with and the recipe's stands (DESIGN.md 3.6.6, 3.6.7).
            planned[name] = PlannedRequirement(as_written, provenance, comments)
        elif block.section == "host" and name in pinned:
            # conda-forge's global pinning already states this package's
            # version, and a bound would take it out of the build matrix.
            # `note` is dropped with the bound: nothing was chosen.
            planned[name] = PlannedRequirement(name, provenance, comments)
        elif noarch and not per_platform:
            comments = ((f"# {note}",) if note else ()) + comments
            planned[name] = PlannedRequirement(
                _requirement_text(name, result.specifier), provenance, comments
            )
        else:
            # The note is carried the same way on the per-platform path, where
            # it survives only when every platform agreed and the line is a
            # plain one. Nothing was chosen on the arch path, so `note` is None
            # there and this adds nothing (DESIGN.md 3.3.1.1).
            comments = ((f"# {note}",) if note else ()) + comments
            planned[name] = _from_split(name, split, provenance, comments)
        for expansion, detail, source in _expansions(variants, config):
            expanded = parse_line(expansion)
            planned.setdefault(
                expanded.name,
                PlannedRequirement(
                    expanded.rendered,
                    Provenance("config-add", f"{detail} ({source})"),
                ),
            )

    # Names this section already states inside a condition. An entry for one of
    # them explains the line that is there; it does not ask for a second,
    # unconditional one (DESIGN.md 3.3.4, 4).
    conditional = _conditionally_stated(block)
    carried: list[AddedRequirement] = []
    for addition in added:
        # Keyed exactly as a recipe line is (`_planned_key`), build string
        # included. Keyed on the bare name instead, an entry carrying one --
        # `esmf ==${{ version }} nompi_*` -- files under `esmf` while the line
        # it explains files under `esmf * nompi_*`, so both are rendered and
        # the recipe grows a second copy of the dependency it already had.
        line = parse_line(addition.text)
        if line.name in conditional:
            continue
        if addition.temporary:
            carried.append(addition)
        planned.setdefault(
            spec_key(line.name, line.build_string),
            PlannedRequirement(
                line.rendered, Provenance("config-add", addition.source)
            ),
        )

    removals: list[Removal] = []
    unexplained: list[Unexplained] = []
    previous_index = (
        build_index(
            previous,
            listed_extras,
            resolver,
            core=core,
            section=block.section,
            output=label or output,
            from_extras=from_extras,
        )
        if previous is not None
        else None
    )

    preserved: dict[str, tuple[str, ...]] = {}
    for position, entry in enumerate(block.content.entries):
        if isinstance(entry, Conditional):
            key, kept, unaccounted, retired = _existing_conditional(
                entry,
                position,
                block,
                planned,
                index,
                config,
                added,
                pinned,
                label,
                settled_names,
            )
            if retired is not None:
                # Config named every dependency inside, so the entry is
                # accounted for and goes, exactly as a retired plain line
                # does -- and is not also reported to G1.
                removals.append(retired)
                continue
            preserved[key] = maintainer_comments(entry.comments)
            unexplained.extend(unaccounted)
            if kept is not None:
                planned[key] = kept
            continue

        requirement = entry
        line = parse_line(entry.text)
        explanation = attribute(line, index, config.recipe_owned, added)
        pending = explanation if isinstance(explanation, Unexplained) else None
        key = _planned_key(line, explanation, block.section, pinned)

        if line.platform_expansions and isinstance(explanation, Provenance):
            # The `noarch_platform` idiom, read but **not authored**. swage
            # now understands that this line already delivers the dependency,
            # so the plan's own copy of it is dropped and the maintainer's
            # spelling is kept exactly as written.
            #
            # Rewriting it into `if: win` / `then: colorama` would be correct
            # and would still be wrong: the two spellings say the same thing,
            # conda-smithy's linter accepts both, and which one a recipe uses
            # is the maintainer's call rather than swage's. Without this the
            # line was kept *and* the plan's version added beside it, so the
            # dependency appeared twice.
            for expansion in line.platform_expansions:
                if not parse_line(expansion).recipe_owned(config.recipe_owned):
                    planned.pop(expansion, None)
            preserved[key] = maintainer_comments(requirement.comments)
            planned[key] = PlannedRequirement(entry.text, explanation)
            continue
        # Last of several lines mapping to one planned line, not first. Two
        # recipe lines collapse into one wherever an `embedded_extras`
        # expansion repeats a dependency upstream also declares -- and the
        # comments above the *first* of those are about the expansion, which
        # swage now delimits with its own markers, so carrying them to a line
        # further down the section re-anchors a remark to something it was
        # never about.
        preserved[key] = maintainer_comments(requirement.comments)

        if key in planned:
            # Upstream still asks for it, so the reconciled line replaces this
            # one -- constraint and all. A bound the recipe states and the plan
            # does not is drift swage reconciles like any other difference, and
            # the one somebody means to keep is in config (DESIGN.md 3.3.14).
            #
            # Unless the recipe wrote the same bound as a template, in which
            # case there is no difference to reconcile and the template stays.
            kept_template = _kept_template(entry.text, planned[key], context)
            if kept_template is not None:
                planned[key] = kept_template
            if pending is not None:
                unexplained.append(pending)
            continue

        removal = classify_removal(
            line,
            index,
            config.recipe_owned,
            previous=previous_index,
            previous_known=previous is not None,
            version=upstream.version,
            retire=config.retire,
        )
        removals.append(removal)
        # A retired line is accounted for -- config said what it is -- so it is
        # removed rather than also reported to G1. Every other unexplained line
        # is still reported, including one swage is dropping because upstream
        # did: that it is going is not the same as it being explained.
        if pending is not None and removal.fate != "retired":
            unexplained.append(pending)
        if removal.removed:
            continue
        # Kept: recipe-owned structure, or something swage will not delete.
        planned[key] = PlannedRequirement(
            line.rendered,
            explanation
            if isinstance(explanation, Provenance)
            else Provenance("recipe-kept", KEPT_UNEXPLAINED),
        )

    planned = _with_preserved_comments(planned, preserved)
    ordered = order_requirements(tuple(planned.values()), index.order)
    annotated = _with_extra_headers(ordered, listed_extras, core)
    entries, generated = _with_expansion_markers(annotated)
    # A remark at the end of a section has no requirement below it to be
    # anchored to (DESIGN.md 6.1), and swage renders the section -- so without
    # this it is deleted. It is the same rule as everywhere else, generated
    # first and preserved after, applied to the one position where what swage
    # generates is a marker rather than a note.
    trailing = _in_reading_order(
        generated, maintainer_comments(block.content.trailing_comments)
    )
    return PlannedSection(
        path=block.path,
        section=block.section,
        where=section_phrase(block.section, label or output),
        entries=entries,
        removals=tuple(removals),
        unexplained=tuple(unexplained),
        overrides=tuple(applied),
        overruled=tuple(settled),
        temporary_additions=tuple(carried),
        trailing_comments=trailing,
    )


def _conditionally_stated(block: RequirementsBlock) -> frozenset[str]:
    """Package names this section states inside a condition.

    An `add_requirements` entry does two jobs: it explains a line the recipe
    has, and it adds one the recipe lacks. Where the recipe states the
    dependency *conditionally* only the first applies -- `cassandra-driver`
    builds `libev` on everything but Windows, and `mpas_tools` takes
    `llvm-openmp` on macOS.

    Without this the entry manufactures a plain line beside the conditional
    one, which is worse than useless: `_existing_conditional` sees the plan
    asking for the dependency unconditionally, concludes swage would delete a
    condition it did not author, and refuses the whole section. Declaring the
    dependency turned a feedstock swage could plan into one it would not touch.

    Names rather than keys, and every branch rather than the taken one: what
    matters is that somebody conditioned this dependency at all.
    """
    return frozenset(
        parse_line(requirement.text).name
        for entry in block.content.entries
        if isinstance(entry, Conditional)
        for requirement in _inside(entry)
    )


def _kept_template(
    text: str, planned: PlannedEntry, context: Mapping[str, str]
) -> PlannedRequirement | None:
    """The recipe's own line, where its template already says what swage would.

    A recipe frequently writes a sibling output's version as
    `apache-airflow-task-sdk ==${{ task_sdk_version }}` rather than as a
    literal, so that one `context` entry keeps the source URL, the built
    package's version and this requirement in step. Rendering the literal is
    not wrong -- it says the same thing today -- but it replaces a maintainer's
    single point of truth with three copies of a number, in a recipe swage does
    not own, and it does it on every feedstock that uses the idiom.

    So the template survives exactly when it is *equivalent*: it resolves,
    through the recipe's own context, to the line swage was about to write.
    Anything else -- a template resolving to a different bound, one swage
    cannot resolve at all -- is reconciled like any other drift, because then
    the recipe and upstream really do disagree and upstream wins
    (DESIGN.md 3.3.14).
    """
    if "${{" not in text or not isinstance(planned, PlannedRequirement):
        return None
    resolved = resolve_expression(text, context)
    if resolved is None or not _same_requirement(resolved, planned.text):
        return None
    return replace(planned, text=text)


def _same_requirement(one: str, other: str) -> bool:
    """Whether two requirement lines say the same thing.

    Whitespace-insensitive, because a recipe writes the operator both ways and
    `== ${{ version }}` is as ordinary a spelling as `==${{ version }}`.
    Comparing the rendered forms directly missed the first of those -- it
    resolves to `apache-airflow-core == 3.3.1` against a planned
    `apache-airflow-core ==3.3.1` -- so `airflow` had one such line preserved
    and an identical one flattened in the same recipe, which is how the gap
    showed.
    """
    return "".join(one.split()) == "".join(other.split())


def _unversioned_reader_line(
    block: RequirementsBlock,
    upstream: UpstreamMetadata,
    name: str,
    variants: Sequence[UpstreamRequirement],
) -> str | None:
    """The recipe's own line, where upstream has no version to reconcile.

    `UpstreamMetadata.states_versions` is what separates "upstream declares
    this package unbounded" from "upstream's build system cannot state a
    bound". Only the second reaches here, and only for a declaration that
    carries no specifier of its own -- a `find_package(GDAL 3.4)` is upstream
    speaking and reconciles like anything else.

    Returned as the recipe wrote it, template and all: `include-what-you-use`
    holds `llvmdev` and `clangdev` to one LLVM series through a single
    `llvm_version` in `context`, and rendering the literal would replace that
    with two copies of a number.

    Keyed by `spec_key`, like everything else that files a requirement under a
    name, because the mpi corner of the fleet states the same package twice on
    purpose -- a bare `libnetcdf` for the version pin conda-smithy applies, and
    `libnetcdf * ${{ mpi_prefix }}_*` for the build pin. Keyed on the bare name
    the second answers for the first, and six feedstocks acquired two copies of
    the build-pinned line and lost the version pin.

    None where the recipe does not state the package -- there is then no bound
    to keep, and the line swage adds is the plain name.
    """
    if upstream.states_versions or any(variant.specifier for variant in variants):
        return None
    for text in _every_name(block):
        line = parse_line(text)
        if spec_key(normalize_name(line.name), line.build_string) == name:
            return text
    return None


def _requirement_text(name: str, specifier: str) -> str:
    return f"{name} {specifier}" if specifier else name


def _existing_conditional(
    entry: Conditional,
    position: int,
    block: RequirementsBlock,
    planned: Mapping[str, PlannedEntry],
    index: AttributionIndex,
    config: FeedstockConfig,
    added: Sequence[AddedRequirement],
    pinned: Container[str] = frozenset(),
    label: str = "",
    overruled: Container[str] = frozenset(),
) -> tuple[str, PlannedEntry | None, tuple[Unexplained, ...], Removal | None]:
    """What becomes of a conditional entry the recipe already has.

    Four outcomes, and which one applies is decided by what the plan says
    about the dependencies *inside* the entry.

    **Replaced**, where swage plans one of them conditionally: it derived the
    same structure from upstream's markers, so what it writes supersedes what
    is there. That is the ordinary case on a recipe swage has written before,
    and the reason a second run is a no-op.

    **Refused**, where swage plans one of them as a plain line. Somebody
    conditioned this dependency and upstream's metadata does not say why --
    rendering the plan would delete the condition, which is a packaging
    decision rather than a reconciliation (DESIGN.md 3.3.4). Unless the
    decision is already on the record: a package in ``overruled`` was
    conditioned because upstream's bounds for it disagree by python, and
    config has since said which bound this one noarch package states, so the
    condition is what that entry replaces.

    **Retired**, where `retire` covers *every* dependency inside it and
    upstream declares none of them here. The rule swage otherwise follows is
    that it does not delete a structure it did not author on evidence about
    one of the names inside it -- but config has spoken about all of them,
    which leaves the entry stating nothing. `colorlog` conditions `colorama`
    on Windows in `host`, where upstream declares it only to run; `dulwich`
    conditions `setuptools` on python 3.12 and up, where upstream declares it
    only to build with. All or nothing, like every other allowlist here: an
    entry naming one retired dependency beside one somebody still means is
    preserved whole, because removing it would take the second away.

    **Preserved** otherwise, exactly as read, so it renders back byte for byte.
    Its dependencies are still attributed: a conditional entry nothing explains
    fails G1 like any other line, and the feedstock is held for a human rather
    than merged.

    The key is the planned name where the entry is replaced, and its position
    in the section otherwise. Position rather than the first name inside,
    because several preserved conditionals can name the same package first --
    `libnetcdf` has two `mpi != "nompi"` entries in one section -- and keying
    them alike would silently drop one.
    """
    lines = [parse_line(requirement.text) for requirement in _inside(entry)]
    explanations = [
        attribute(line, index, config.recipe_owned, added) for line in lines
    ]
    keys = [
        _planned_key(line, explanation, block.section, pinned)
        for line, explanation in zip(lines, explanations, strict=True)
    ]

    for key, explanation in zip(keys, explanations, strict=True):
        replacement = planned.get(key)
        if replacement is None:
            continue
        if isinstance(replacement, PlannedRequirement):
            if replacement.name in overruled:
                # The recipe conditioned this package because upstream's own
                # bounds for it disagree across pythons, and an
                # `overruled_constraints` entry is the decision that one
                # package states one of them (DESIGN.md 3.3.2). Refusing here
                # would be asking again about the decision that entry *is*.
                return key, None, (), None
            blessed = _blessed_variant(entry, replacement.name, config)
            if blessed is None:
                raise PlanError(
                    _condition_would_be_lost(block, entry, replacement, config, label)
                )
            # The condition is conda-forge's build variant and config says it
            # covers this package, so upstream's unconditional declaration
            # explains the line that is there rather than asking for a second
            # one without the condition. Keyed on the planned name so this
            # entry takes its slot: keyed by position, the plan would render
            # both.
            return (
                key,
                PlannedConditional(
                    (replace(entry, comments=()),),
                    _under_variant(explanation, blessed),
                    preserved=True,
                ),
                tuple(item for item in explanations if isinstance(item, Unexplained)),
                None,
            )
        return key, None, (), None

    retired = _retired_conditional(entry, lines, index, config)
    if retired is not None:
        return f"{_CONDITIONAL}{position}", None, (), retired

    return (
        f"{_CONDITIONAL}{position}",
        PlannedConditional(
            (replace(entry, comments=()),),
            next(
                (item for item in explanations if isinstance(item, Provenance)),
                Provenance("recipe-kept", KEPT_UNEXPLAINED),
            ),
            preserved=True,
        ),
        tuple(item for item in explanations if isinstance(item, Unexplained)),
        None,
    )


def _blessed_variant(
    entry: Conditional, package: str, config: FeedstockConfig
) -> VariantCondition | None:
    """The config entry blessing this condition *for this package*, or None.

    Both halves, because a condition on its own would bless whatever
    upstream-declared dependency happened to sit inside it. `esmf`'s
    `mpi != "nompi"` block is about `parallelio`; a package moved into it later
    is a claim nobody made, and swage should refuse it exactly as it refuses an
    unblessed condition.

    The condition is matched with whitespace normalized, so a recipe writing
    `mpi!="nompi"` and a config file writing `mpi != "nompi"` are the same
    condition. Nothing else is: quoting is left alone because a recipe that
    writes `'nompi'` where config writes `"nompi"` is a difference somebody
    should look at, and evaluating the expression would be inventing an answer
    for conditions nobody blessed.
    """
    written = _normalized_condition(entry.condition)
    for blessed in config.variant_conditions:
        if _normalized_condition(blessed.condition) == written and blessed.covers(
            package
        ):
            return blessed
    return None


def _under_variant(explanation: Attribution, blessed: VariantCondition) -> Provenance:
    """The provenance for a line kept inside a blessed build variant.

    It says both things: the dependency is upstream's, and the condition
    around it is one config accounts for. Without the second half `swage
    explain` printed a bare `upstream` and the entry doing the work was
    invisible -- which is the same question the `packages` list answers in the
    config file, asked from the other side.
    """
    if isinstance(explanation, Provenance):
        return replace(
            explanation, detail=f"{explanation.detail}, under if: {blessed.condition}"
        )
    return Provenance("recipe-kept", KEPT_UNEXPLAINED)


def _normalized_condition(condition: str) -> str:
    return "".join(condition.split())


def _retired_conditional(
    entry: Conditional,
    lines: Sequence[ParsedLine],
    index: AttributionIndex,
    config: FeedstockConfig,
) -> Removal | None:
    """The removal for a conditional `retire` accounts for whole, or None.

    The three tests are `classify_removal`'s own, in its order, so that a name
    reaching `retire` here has passed exactly what it passes on a plain line:
    recipe-owned structure is kept by definition, a name upstream still
    declares in this section's role is not an artifact, and only then does
    config get asked.
    """
    if not lines:
        return None
    for line in lines:
        if line.recipe_owned(config.recipe_owned):
            return None
        if index.contains(line.name):
            return None
        if not _retired(line.name, config.retire):
            return None
    names = ", ".join(repr(line.name) for line in lines)
    return Removal(
        fate="retired",
        text=inline_text(entry),
        reason=(
            f"{names} is in this feedstock's `retire` list and no upstream "
            "version declares it, so the conditional entry stating it is "
            "removed as the artifact config says it is"
        ),
    )


def _retired(name: str, retire: Container[str]) -> bool:
    return name in retire or normalize_name(name) in retire


#: How a preserved conditional is keyed in the planned section. Not a name:
#: what it is keyed by is where it was.
_CONDITIONAL = "conditional:"


def _every_name(block: RequirementsBlock) -> tuple[str, ...]:
    """Every requirement a section states, conditional branches included.

    `texts()` reads the plain entries only, and in a `build` section that is
    the compilers -- the cross-compilation block's own contents are precisely
    what sits inside a conditional. Reading it that way made the mirrored
    names invisible, which is the one thing this had to see.
    """
    texts = list(block.content.texts())
    for entry in block.content.conditionals:
        texts.extend(item.text for item in _inside(entry))
    return tuple(texts)


def _inside(entry: Conditional) -> tuple[Requirement, ...]:
    """Every plain requirement in a conditional's branches, nesting included."""
    found: list[Requirement] = []
    for branch in (entry.then, entry.otherwise or ()):
        for item in branch:
            if isinstance(item, Requirement):
                found.append(item)
            else:
                found.extend(_inside(item))
    return tuple(found)


def _condition_would_be_lost(
    block: RequirementsBlock,
    entry: Conditional,
    replacement: PlannedRequirement,
    config: FeedstockConfig,
    label: str = "",
) -> str:
    """The message for a condition swage would delete rather than reconcile.

    Two cases and two different remedies. Where nothing blesses the condition,
    the question is whether it is conda-forge's build variant at all. Where
    something blesses it for other packages, that question is already answered
    and the open one is narrower -- whether this package belongs in the list
    too -- so saying "resolve by hand" there would send a maintainer back to a
    decision they have already made.
    """
    where = section_phrase(block.section, label)
    blessed = next(
        (
            item
            for item in config.variant_conditions
            if _normalized_condition(item.condition)
            == _normalized_condition(entry.condition)
        ),
        None,
    )
    if blessed is not None:
        return (
            f"cannot plan {where}: it states {replacement.name!r} under a "
            "condition config blesses for other packages\n"
            f"    if: {entry.condition}\n"
            f"  config accounts for this condition around "
            f"{', '.join(blessed.packages)}, and upstream asks for "
            f"{replacement.name!r} on every build this output produces\n"
            f"  add {replacement.name!r} to that entry's `packages` if it "
            "belongs there too, or move the line out of the condition"
        )
    return (
        f"cannot plan {where}: it states {replacement.name!r} conditionally "
        "and upstream does not\n"
        f"    if: {entry.condition}\n"
        f"  upstream asks for it on every build this output produces, so swage "
        f"would write one unconditional line -- {replacement.text} -- and the "
        "condition would be gone\n"
        "  keeping it is a decision about what the package promises, so swage "
        "makes neither: resolve by hand, or say the condition selects a build "
        "variant with `variant_conditions` in config"
    )


def _from_split(
    name: str, split: Split, provenance: Provenance, comments: tuple[str, ...]
) -> PlannedEntry:
    """Render one dependency's python ranges as the entries a section holds.

    Three shapes, and the first two are the ones that occur. Upstream saying
    the same thing on every python is a plain line, and the great majority of
    dependencies are that. Upstream splitting at one version is one entry with
    an `else:` -- the concise spelling of what `apache-beam` hand-writes as two
    `if:` entries, and the shape conda-forge's own tooling normalizes toward.
    Splitting at two or more versions cannot be written with a single `else:`
    and stays one entry per range.
    """
    if len(split.branches) == 1 and split.branches[0].condition is None:
        return PlannedRequirement(
            _requirement_text(name, split.branches[0].specifier), provenance, comments
        )
    if split.complementary:
        first, second = split.branches
        return PlannedConditional(
            (
                Conditional(
                    condition=str(first.condition),
                    then=(Requirement(_requirement_text(name, first.specifier)),),
                    otherwise=(Requirement(_requirement_text(name, second.specifier)),),
                    then_inline=True,
                    otherwise_inline=True,
                ),
            ),
            provenance,
            comments,
        )
    return PlannedConditional(
        tuple(
            Conditional(
                condition=str(branch.condition),
                then=(Requirement(_requirement_text(name, branch.specifier)),),
                then_inline=True,
            )
            for branch in split.branches
        ),
        provenance,
        comments,
    )


def _implicit_backend(
    section: str,
    upstream: UpstreamMetadata,
    config: FeedstockConfig,
    core: bool,
) -> tuple[AddedRequirement, ...]:
    """What `host` is built with when upstream declares no build system.

    This is the one place the absent/empty distinction DESIGN.md 3.6.2 is so
    careful about actually pays out. `build_requires is None` means upstream
    told swage nothing, and PEP 517 already says what that means: the project
    is built with the legacy setuptools backend. An empty tuple means upstream
    said it needs nothing, which is a different claim and gets nothing added.

    It is deliberately routed through the same mechanism as
    `add_requirements`, so the line arrives with a provenance naming the file
    that asked for it and G1 explains it like any other config-supplied
    requirement. Nothing here overrides a maintainer: a project that names
    hatchling or poetry-core has `build_requires`, so this never runs for it.

    **A metapackage output gets nothing**, the same as one whose upstream
    declares a backend. `core` is false for an `extras_as_outputs` output, and
    such an output builds nothing from source -- its `host` carries `python`
    and no backend, which all 18 of the amazon provider's do. Where the
    backend was *declared* that already held, because `_upstream_groups`
    honors `core`; where it is inferred from silence it did not, and the
    difference showed up as `setuptools` added to each of `parsl`'s twelve
    metapackages.
    """
    if section != "host" or not core or upstream.build_requires is not None:
        return ()
    return tuple(
        AddedRequirement(
            text,
            "config/defaults.yaml: default_build_requires "
            "(upstream declares no build system)",
        )
        for text in config.default_build_requires
    )


def _not_packaged(
    name: str, variants: Sequence[UpstreamRequirement], config: FeedstockConfig
) -> NotPackaged | None:
    """Config's record that conda-forge has no package for this dependency.

    Looked up by the names *upstream* wrote as well as by the group's key,
    because the two differ in exactly the case this key is for: a name nothing
    resolves is grouped under upstream's spelling, and one that resolves is
    grouped under conda-forge's. Matching only the key would leave the entry
    silently idle the day the package appeared, which is the state the caller
    raises on.
    """
    entries = config.not_packaged
    if not entries:
        return None
    for spelling in (name, *(variant.name for variant in variants)):
        entry = entries.get(spelling) or entries.get(normalize_name(spelling))
        if entry is not None:
            return entry
    return None


def _now_packaged(name: str, conda_name: str, config: FeedstockConfig) -> str:
    """An entry for a dependency conda-forge has since started packaging."""
    where = (
        f"config/feedstocks/{config.feedstock}.yaml"
        if config.feedstock
        else "this feedstock's config file"
    )
    return (
        f"`not_packaged` says conda-forge has no {name!r}, and it now resolves "
        f"to {conda_name!r}\n"
        "  that entry is the only thing keeping the dependency out of this "
        "recipe, and there is no\n"
        f"  longer a reason for it to be -- drop it in {where}"
    )


def _upstream_groups(
    upstream: UpstreamMetadata,
    listed_extras: Sequence[str],
    resolver: NameResolver,
    section: str,
    core: bool,
    embedded_extras: Layered[tuple[str, ...]] | None = None,
    from_extras: Mapping[str, frozenset[str]] | None = None,
) -> list[tuple[str, list[UpstreamRequirement], Provenance]]:
    """Group upstream's requirements by the conda name they resolve to.

    Core wins over an extra where a package appears in both, matching
    attribution's order (DESIGN.md 3.3.10) so a line's provenance does not
    depend on which code looked at it.
    """
    groups: dict[str, list[UpstreamRequirement]] = {}
    provenance: dict[str, Provenance] = {}

    def add(requirement: UpstreamRequirement, origin: Provenance) -> None:
        resolution = resolve_requirement(
            requirement, resolver, embedded_extras, upstream.conda_names
        )
        name = resolution.conda_name if resolution else requirement.name
        groups.setdefault(name, []).append(requirement)
        first = provenance.get(name)
        if first is None:
            provenance[name] = Provenance(origin.origin, origin.detail, resolution)
        elif resolution is not None and resolution.dropped_extras:
            # A group can collect a plain requirement and one carrying an
            # unaccounted extra -- upstream declaring `foo` and `foo[bar]` with
            # nothing mapping the second. One line is rendered for both, so the
            # extra is as dropped as it would be alone, and keeping only the
            # first provenance would leave G2 with nothing to stop on. The
            # origin still comes from the first, because which *list* explains
            # the line is a separate question that core still wins.
            provenance[name] = Provenance(first.origin, first.detail, resolution)

    if core:
        source = (
            (upstream.build_requires or ())
            if section == "host"
            else upstream.dependencies
        )
        for requirement in source:
            add(requirement, Provenance("upstream-core", "upstream"))

    if section != "host":
        taken = from_extras or {}
        for extra in listed_extras:
            for requirement in upstream.optional_dependencies.get(extra, ()):
                resolution = resolve_requirement(requirement, resolver, embedded_extras)
                conda_name = resolution.conda_name if resolution else requirement.name
                if not drawn_on(requirement, conda_name, extra, taken):
                    continue
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


def _settled_captions(
    variants: Sequence[UpstreamRequirement], config: FeedstockConfig
) -> tuple[str, ...]:
    """Captions for the extras config settled as pulling nothing in.

    **Absent and empty are different claims**, the same distinction DESIGN.md
    3.6.2 draws for `[build-system] requires`. An absent `embedded_extras`
    entry means nobody has looked at the extra yet, which G2 stops the
    feedstock over; an empty one means somebody did, and conda-forge needs
    nothing beyond the bare dependency. Only the second is a decision, and only
    a decision earns a line in the recipe.

    This is what the prior tools wrote as an empty `# start` / `# end` pair
    around no lines at all -- 13 of the 22 marker pairs in the fleet's
    checkouts are that shape, so recording a *negative* answer is the
    mechanism's commonest use rather than an edge case. A pair delimiting
    nothing states the conclusion only by implication; one line says it, and
    says it where the reader's question actually arises, on the dependency
    whose extra it is about.

    Deduplicated on the key, because a requirement declared in both core and a
    listed extra reaches this twice and would otherwise caption its line twice.
    """
    settled: dict[str, None] = {}
    for requirement in variants:
        if not requirement.extras:
            continue
        entry = config.embedded_extras.lookup(requirement.key)
        # `entry[0]` is the expansion; empty is the decision, absent is silence.
        if entry is not None and not entry[0]:
            settled.setdefault(requirement.key)
    return tuple(f"# {key} needs nothing extra on conda-forge" for key in settled)


def _planned_key(
    line: ParsedLine,
    explanation: Attribution,
    section: str = "",
    pinned: Container[str] = frozenset(),
) -> str:
    """The name the plan renders this recipe line under.

    Not always the name the line is written under, and the gap is where a
    dependency gets rendered twice. A recipe routinely spells a package the way
    *upstream* does where conda-forge publishes it under another name --
    `pyOpenSSL` for `pyopenssl`, `psycopg2-binary` for `psycopg2` -- because
    the tools swage replaces did not resolve the name at all. Keyed on the
    line's own spelling, the reconciled line and the line already in the recipe
    look like two different dependencies: swage renders both, one requirement
    wearing two lines.

    **Nothing downstream catches that.** Both lines attribute to the same
    upstream declaration, so both carry a `Provenance` and G1 is satisfied;
    both resolve exactly, so G2 is too. `apache-airflow-providers-snowflake`
    would have been pushed carrying `pyopenssl` and `pyOpenSSL` side by side.

    The resolution that attributed the line already says where it belongs, and
    it is the same `Resolution` the planned entry was keyed on, so the two
    cannot disagree. Where there is none -- structure, an unresolved name, or a
    line nothing explains -- the line's own name is all there is to go on.

    **A build string is part of the key**, because a line carrying one is a
    different requirement from the same package without one rather than
    another spelling of it. `esmf` states `hdf5` and `hdf5 * ${{ mpi_prefix
    }}_*` in one `host` section on purpose -- the first takes the version
    pinning from conda-forge's variants and the second the build pinning from
    the mpi variant -- and keyed on the name alone the second read as a
    constraint change to the first, so swage rewrote `hdf5 * ${{ mpi_prefix
    }}_*` to `hdf5` and the mpi pin left the recipe (DESIGN.md 3.3.6).

    **So is a constraint, on a `host` line naming a package the pinning
    covers**, and for the same reason one step further along. swage plans such
    a package bare, so the recipe may state it twice to different ends: the
    bare line takes conda-forge's pin, and a bounded one beside it asserts
    that the pin falls inside the range upstream asked for. Keyed alike the
    second would read as a constraint change to the first and the assertion
    would go. Nothing in this fleet writes the pair today -- 0 sections of 618
    such lines -- which is exactly why it is worth keying apart now, while
    there is nothing to regress.

    **Only where there is no build string**, because a line carrying one is
    already keyed apart by it, and its version field is the `*` placeholder
    a match spec needs to reach its third field rather than a bound anybody
    wrote. `esmf` writes `hdf5 * ${{ mpi_prefix }}_*`; counting that `*` as
    a constraint split the line from the `add_requirements` entry that
    explains it, and the section rendered the pin twice.
    """
    if isinstance(explanation, Provenance) and explanation.mapping is not None:
        name = explanation.mapping.conda_name
    else:
        name = line.name
    if (
        section == "host"
        and name in pinned
        and line.constraint
        and not line.build_string
    ):
        return f"{spec_key(name, line.build_string)} {line.constraint}"
    return spec_key(name, line.build_string)


def _with_preserved_comments(
    planned: dict[str, PlannedEntry],
    preserved: Mapping[str, tuple[str, ...]],
) -> dict[str, PlannedEntry]:
    """Carry each requirement's maintainer-written comments onto its new line.

    DESIGN.md 6.1: a requirement's rendered comments are the ones swage
    generates this run, then every comment the recipe had above it that swage
    did not write. Applied here, once, rather than at each of the four places a
    `PlannedRequirement` is built -- an upstream line, an `embedded_extras`
    expansion, an `add_requirements` entry and a kept line -- because a rule
    about what a section looks like should not depend on which branch produced
    the line.

    Doing it per branch is what the previous behavior amounted to, and it was
    not merely lossy but inconsistent: a kept line carried `requirement.comments`
    through untouched while an upstream line had them replaced by whatever was
    generated for it, usually nothing. So a note survived above a dependency
    swage could not explain and was destroyed above one it could --
    `google-cloud-bigquery`'s note about `google-auth[pyopenssl]` being of the
    second kind.

    Generated comments come first because they are structural: a block header
    partitions the section and has to lead it, and swage's own note about the
    line reads as a caption above the maintainer's remark rather than below it.

    **Blank lines are the exception, and they are not comments.** A blank line
    is spacing between groups of requirements, so it belongs above everything
    attached to the line below it. Ordered with the rest of the preserved
    comments it lands *between* swage's note and the dependency the note is
    about, which detaches the two -- `apache-airflow-providers-google` has a
    blank line above a marker note and would have been rendered that way.

    ``preserved`` is keyed by the name the *plan* renders each recipe line
    under (`_planned_key`) rather than by the line's own spelling, and is built
    by the caller as it attributes each line. Recomputing it here from the
    block was a second, subtly different answer to "which planned line is this
    recipe line": a note above `pyOpenSSL` belongs to whatever swage renders
    for that requirement, including when it renders it as `pyopenssl`.
    """
    return {
        name: replace(
            entry,
            comments=_in_reading_order(entry.comments, preserved.get(name, ())),
        )
        for name, entry in planned.items()
    }


def _in_reading_order(
    generated: tuple[str, ...], preserved: tuple[str, ...]
) -> tuple[str, ...]:
    """Spacing, then what swage generated, then what the maintainer wrote."""
    blanks = 0
    while blanks < len(preserved) and not preserved[blanks].strip():
        blanks += 1
    return (*preserved[:blanks], *generated, *preserved[blanks:])


def _with_extra_headers(
    ordered: Sequence[PlannedEntry],
    listed_extras: Sequence[str],
    core: bool,
) -> tuple[PlannedEntry, ...]:
    """Introduce each extra's dependencies with a header naming it (DESIGN.md 6).

    A header runs until the next header or the end of the section, so one
    comment per *extra* rather than per dependency: `google-cloud-bigquery`
    folds in nine extras, and annotating each of its twenty-odd lines
    individually would bury the recipe in redundancy.

    **Only where the section holds lines from more than one source**, which is
    what the header is for: it answers "which extra did this come from", and
    that question only exists when there is more than one possible answer. An
    `extras_as_outputs` output draws exactly one extra and no core
    dependencies, and its *name* already says which -- so
    `apache-airflow-providers-common-sql-with-pandas` would carry
    `# from the pandas extra` above every line it has, which is why none of
    the published provider recipes do. Getting this wrong is invisible to a
    test that compares dependency lines, and shows up the moment a rendering
    is compared to a real recipe byte for byte.
    """
    if not core and len(listed_extras) < 2:
        return tuple(ordered)

    result: list[PlannedEntry] = []
    current: str | None = None
    for entry in ordered:
        extra = (
            entry.provenance.detail.removeprefix("extra:")
            if entry.provenance.origin == "upstream-extra"
            else None
        )
        if extra is not None and extra != current:
            # Below any blank lines the recipe already had, for `_in_reading
            # order`'s reason: a blank line is spacing above the whole group,
            # and a header inserted above it lands one group too early --
            # `airflow`'s `apache-airflow-core-with-all` rendered
            # `# from the kerberos extra`, a blank line, and then the extra's
            # three dependencies.
            blanks = 0
            while blanks < len(entry.comments) and not entry.comments[blanks].strip():
                blanks += 1
            entry = replace(
                entry,
                comments=(
                    *entry.comments[:blanks],
                    f"# from the {extra} extra",
                    *entry.comments[blanks:],
                ),
            )
        current = extra
        result.append(entry)
    return tuple(result)


#: How `_expansions` labels a line it spliced in, which is what lets the
#: markers below find those lines again after ordering has moved them.
_EMBEDDED = "embedded_extras:"


def _embedded_key(entry: PlannedEntry) -> str | None:
    """The `name[extra]` an expansion line stands in for, or None."""
    provenance = entry.provenance
    if provenance.origin != "config-add" or not provenance.detail.startswith(_EMBEDDED):
        return None
    return provenance.detail[len(_EMBEDDED) :].partition(" (")[0]


def _with_expansion_markers(
    ordered: Sequence[PlannedEntry],
) -> tuple[tuple[PlannedEntry, ...], tuple[str, ...]]:
    """Wrap each embedded-extras expansion in its `# start`/`# end` pair.

    These markers are what make the embedding round-trippable (DESIGN.md 6):
    they let a later run find the block it wrote last time, replace its
    contents, and leave hand-written lines outside the markers alone. Without
    them a rerun cannot tell an expansion from a line somebody added by hand,
    so the first thing swage would do to a recipe carrying them is delete
    them -- which is what the published `pyhive[hive-pure-sasl]`,
    `celery[redis]`, `pandas[sql-other]` and `psycopg[binary]` blocks in the
    corpus would have lost.

    The pair is not symmetric in where it can live. `# start` sits above the
    first expanded line like any other comment, but `# end` belongs *below*
    the last one -- so it becomes the leading comment of whatever follows, or
    the section's trailing comment where the expansion runs to the end. The
    recipe reader already models both, which is how the round trip closes.
    """
    result: list[PlannedEntry] = []
    open_key: str | None = None
    for entry in ordered:
        key = _embedded_key(entry)
        before: list[str] = []
        if open_key is not None and key != open_key:
            before.append(f"# end {open_key}")
            open_key = None
        if key is not None and key != open_key:
            before.append(f"# start {key}")
            open_key = key
        result.append(
            replace(entry, comments=(*before, *entry.comments)) if before else entry
        )
    trailing = (f"# end {open_key}",) if open_key is not None else ()
    return tuple(result), trailing


#: How the fleet says "this build runs on one platform and targets another".
#: Every one of the 19 outputs in the maintainer's checkouts with such a block
#: writes the condition this way, alone or joined to an mpi variant with `and`.
_CROSS = "build_platform != target_platform"


#: The `recipe-kept` detail on a line swage carries through a cross-compiled
#: output's `build` section untouched, and on the copy it keeps in step. Both
#: are the recipe's own lines: the first says swage rewrote the section around
#: it and changed nothing, the second says what it changed and why.
_BUILD_CARRIED = "the build section, as the recipe has it"
_BUILD_MIRRORED = "kept in step with the host requirement it copies"


def _mirrors(
    recipe: Recipe, sections: Sequence[PlannedSection]
) -> tuple[tuple[PlannedSection, ...], Mapping[str, frozenset[str]]]:
    """`build` sections whose copy of a `host` line swage is keeping in step.

    A cross-compilation block repeats `host` requirements so the build tools
    resolve for the platform doing the building, and swage reconciles the
    `host` line the copy was made from. Left alone the copy goes stale:
    `oracledb` states `cython >=3.2,<4` in both places, upstream now says
    `~=3.2`, and a swage that writes one and not the other leaves the recipe
    saying two things about one dependency (DESIGN.md 3.3.6.1).

    **Only where the two currently agree**, which is the whole of the rule.
    A copy that already differs from the line it copies differs deliberately:
    `netcdf4` and `cartopy` both write a bare `cython` in the block against a
    bounded one in `host`, because what the build platform needs is the tool
    and not the version. swage did not make that difference and has no basis
    to resolve it, so it leaves the copy alone and the gate goes on asking.

    **And only a constraint, never a line.** Whether a `host` requirement
    belongs in the block at all is the judgment 3.3.6.1 leaves open, so swage
    adds nothing here and removes nothing: it edits the copies a maintainer
    already chose to make. The compilers beside them are not touched, and
    there is no code path here that could touch them.

    Returns the sections to write and, per `host` block, the names whose copy
    was kept in step -- which is what stops the gate asking about a mirroring
    swage has already done.
    """
    planned = {section.path: section for section in sections}
    written: list[PlannedSection] = []
    in_step: dict[str, frozenset[str]] = {}
    for output in recipe.outputs:
        build = output.blocks.get("build")
        host = output.blocks.get("host")
        if build is None or host is None:
            continue
        if not any(_CROSS in entry.condition for entry in build.content.conditionals):
            continue
        section = planned.get(host.path)
        if section is None:
            continue
        before = _by_name(host.content.texts())
        after = _by_name(text for entry in section.entries for text in _texts(entry))
        replacements: dict[str, str] = {}
        for copy in _every_name(build):
            line = parse_line(copy)
            name = normalize_name(line.name)
            was, now = before.get(name), after.get(name)
            if was is None or now is None:
                continue
            if parse_line(was).rendered != line.rendered:
                continue
            if parse_line(now).rendered != line.rendered:
                replacements[copy] = now
        if not replacements:
            continue
        in_step[host.path] = frozenset(
            normalize_name(parse_line(copy).name) for copy in replacements
        )
        written.append(
            PlannedSection(
                path=build.path,
                section=build.section,
                where=section_phrase(build.section, output.label),
                entries=tuple(
                    _carried(entry, replacements) for entry in build.content.entries
                ),
                trailing_comments=build.content.trailing_comments,
            )
        )
    return tuple(written), in_step


def _by_name(texts: Iterable[str]) -> dict[str, str]:
    """One section's plain requirements, keyed by the package they name."""
    return {normalize_name(parse_line(text).name): text for text in texts}


def _carried(entry: Entry, replacements: Mapping[str, str]) -> PlannedEntry:
    """One `build` entry as the plan holds it, with any copy brought in step."""
    if isinstance(entry, Requirement):
        text = replacements.get(entry.text, entry.text)
        detail = _BUILD_MIRRORED if text != entry.text else _BUILD_CARRIED
        return PlannedRequirement(
            text, Provenance("recipe-kept", detail), entry.comments
        )
    rewritten = _rewritten(entry, replacements)
    detail = _BUILD_MIRRORED if rewritten != entry else _BUILD_CARRIED
    return PlannedConditional(
        (rewritten,), Provenance("recipe-kept", detail), entry.comments, preserved=True
    )


def _rewritten(entry: Conditional, replacements: Mapping[str, str]) -> Conditional:
    """The same conditional, with the copies inside it brought in step."""

    def branch(entries: tuple[Entry, ...]) -> tuple[Entry, ...]:
        return tuple(
            replace(item, text=replacements[item.text])
            if isinstance(item, Requirement) and item.text in replacements
            else _rewritten(item, replacements)
            if isinstance(item, Conditional)
            else item
            for item in entries
        )

    return replace(
        entry,
        then=branch(entry.then),
        otherwise=None if entry.otherwise is None else branch(entry.otherwise),
    )


def _cross_compiled(
    recipe: Recipe,
    sections: Sequence[PlannedSection],
    config: FeedstockConfig,
    in_step: Mapping[str, frozenset[str]],
) -> tuple[str, ...]:
    """`host` sections swage would change on an output that cross-compiles.

    **15 of the 19 outputs in the fleet with a cross-compilation block repeat a
    `host` requirement inside it** -- `cython`, `numpy`, `cffi`, `pybind11`,
    `maturin`, `grpcio-tools` -- because a cross build resolves its build tools
    for the build platform rather than the target. So a `host` requirement swage
    adds or bumps may need mirroring into `build`, and a recipe that got only
    half of that builds natively and fails cross-compiled.

    **What to mirror is a judgment per dependency, not a set operation**:
    `pyproj` mirrors `cython` and not `proj`; `python-eccodes` mirrors `numpy`
    and `cffi` and not `findlibs`. Until there is a rule for it (DESIGN.md
    3.3.6.1), swage plans the `host` change and holds the feedstock for a human
    rather than merging it unattended.

    **Except where `_mirrors` has already answered it.** A copy the block
    already holds, saying what the `host` line said, is written in step with
    it rather than asked about -- so those names are subtracted here, and a
    change confined to them asks nothing.

    Changes rather than additions, because a bumped bound needs mirroring
    exactly as much as a new line does. **A reordering is neither**, and it is
    what the gate mostly used to fire on: of the 17 outputs it stopped in the
    last fleet audit, 8 had the same requirements in the same words as the
    recipe already stated, in the order 6 puts them in. What mirroring needs to
    know is which requirements the block repeats and under what constraint, and
    a reordering changes neither -- so the comparison is between the
    requirements the two sections hold.

    18 of the 20 blocks in the fleet that repeat two or more `host` names do
    list them in `host`'s order, which is an argument for reordering the block
    to match one day and never an argument for asking a human about it.

    **And a change to a requirement no cross build could want is neither.**
    `setuptools` and the rest of the pure-python packaging machinery are
    imported by the backend that already runs under `cross-python_*`, and the
    fleet says so: `setuptools` sits in 15 of these outputs' `host` sections
    and exactly one repeats it in `build`. So a change confined to names
    `pure_python_build_tools` blesses asks nothing, and the gate holds only
    where a changed name could need a build-platform copy -- which it takes
    to be any name not on that list, plus any name this output's own `build`
    already repeats, whose copy a bump would leave stale.

    The list is config rather than inference for the reason every allowlist
    here is: which packages ship something a build must execute is a fact
    about conda-forge, and a name nobody has checked holds the feedstock.
    """
    changed: list[str] = []
    planned = {section.path: section for section in sections}
    exempt = frozenset(normalize_name(name) for name in config.pure_python_build_tools)
    for output in recipe.outputs:
        build = output.blocks.get("build")
        host = output.blocks.get("host")
        if build is None or host is None:
            continue
        if not any(_CROSS in entry.condition for entry in build.content.conditionals):
            continue
        section = planned.get(host.path)
        if section is None:
            continue
        before = [inline_text(entry) for entry in host.content.entries]
        after = [text for entry in section.entries for text in _texts(entry)]
        if sorted(before) == sorted(after):
            continue
        repeated = {
            normalize_name(parse_line(text).name) for text in _every_name(build)
        }
        # A name that has left `host` altogether and that the block does not
        # repeat leaves nothing behind to go stale: there is no copy to bump
        # and none to delete, and a dependency going away cannot create a need
        # for it on the build platform. `python-ldap` drops `pyasn1` and
        # `pyasn1-modules` from `host` and its block holds neither.
        gone = _named(before) - _named(after)
        moved = (
            _named(set(before) ^ set(after))
            - in_step.get(host.path, frozenset())
            - (gone - repeated)
        )
        if all(name in exempt and name not in repeated for name in moved):
            continue
        changed.append(section_phrase(host.section, output.label))
    return tuple(changed)


def _named(texts: Iterable[str]) -> set[str]:
    """The packages a set of requirement lines names."""
    return {normalize_name(parse_line(text).name) for text in texts}


def _texts(entry: PlannedEntry) -> list[str]:
    """One planned entry as the lines it writes, without comments."""
    if isinstance(entry, PlannedRequirement):
        return [entry.text]
    return [inline_text(conditional) for conditional in entry.conditionals]


def planned_matrices(plan: RecipePlan) -> dict[str, tuple[str, ...]]:
    """The plan as the writer takes it: test path -> the versions it should test.

    The companion to `planned_blocks`, and here for the same reason: this is
    where a plan becomes bytes, and the byte comparison that decides whether
    swage would change anything has to see both kinds of edit or it will call a
    changed recipe unchanged.
    """
    return {matrix.path: matrix.versions for matrix in plan.test_matrices}


def planned_blocks(plan: RecipePlan) -> dict[str, BlockContent]:
    """The plan as the writer takes it: block path -> that section's new body.

    Here rather than at each call site because it is the one place a plan
    becomes bytes, and that is the comparison G7 rests on (DESIGN.md 5.3): a
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
    `comments` the same way and DESIGN.md 6.1 needs only one spelling; they
    land on the first entry, which is where they render.
    """
    if isinstance(entry, PlannedRequirement):
        return (Requirement(entry.text, entry.comments),)
    first, *rest = entry.conditionals
    return (replace(first, comments=entry.comments), *rest)


def accounted_extras(config: FeedstockConfig) -> set[str]:
    """Upstream extras this feedstock's config says something about.

    "Something" rather than "yes": an extra in `skip` is accounted for exactly
    as firmly as one in `supported`, because `skip` is how a decision *not* to
    publish gets recorded (DESIGN.md 4). The point is that somebody considered
    it, not which way they went.

    One definition, read by both the gate that enforces exhaustiveness (G3)
    and the plan field that reports what nothing draws on. Two spellings of
    "accounted for" would eventually disagree, and the disagreement would
    surface as swage nagging about an extra the maintainer had already
    declined -- advice pointing at a decision already on the record.

    **`embedded_extras` says nothing here, and used to.** Its key
    `aiobotocore[boto3]` names a *dependency* and an extra *of that
    dependency*; G3 asks about the extras of the project being packaged. The
    two namespaces are unrelated, so a clause contributing the part before the
    bracket could only ever match by coincidence -- and it does coincide, on
    `apache-airflow-providers-amazon`, whose own upstream extras include
    `aiobotocore` and `pandas` while the family config carries
    `aiobotocore[boto3]` and `pandas[sql-other]` for unrelated reasons. A gate
    satisfied by a name collision is a gate disarmed. The accounting a
    dependency-carried extra actually needs is G2's (DESIGN.md 3.2), where it
    stops swage dropping the extra silently.
    """
    accounted: set[str] = set()
    extras_as_outputs = config.extras_as_outputs
    if extras_as_outputs is not None:
        accounted |= set(extras_as_outputs.supported) | set(extras_as_outputs.skip)
    for output in config.outputs.values():
        # `from_extras` accounts for an extra as firmly as `extras` does: the
        # feedstock publishes it, in pieces, and which output takes which
        # piece is a finer question than the one G3 asks.
        accounted |= (
            set(output.run.extras) | set(output.run.skip) | set(output.run.from_extras)
        )
    return accounted


def declares_skip(config: FeedstockConfig) -> bool:
    """Whether this feedstock opted into exhaustiveness (G3, DESIGN.md 4).

    Declaring a `skip` list is the maintainer saying "I mean to account for
    all of these", and it is what turns G3 from advice into a gate. Either
    shape can say it: `extras_as_outputs.skip` for a feedstock publishing
    extras as outputs of their own, `outputs[].run.skip` for one folding them
    into an existing output.
    """
    extras_as_outputs = config.extras_as_outputs
    if extras_as_outputs is not None and extras_as_outputs.skip:
        return True
    return any(output.run.skip for output in config.outputs.values())


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


def _self_conflicts(
    recipe: Recipe,
    upstream: RecipeUpstream,
    sections: Sequence[PlannedSection],
) -> tuple[SelfConflict, ...]:
    """Requirements on a package this recipe builds, at a version it does not.

    What the recipe builds is not the recipe's own `version` context: a split
    recipe packages several archives, and the version of each is the one in
    the URL the recipe pins and the hash it verifies. That is exactly what
    `RecipeUpstream` already read, so this asks the archives rather than
    parsing `context` (DESIGN.md 3.6).

    An unevaluable constraint is passed over rather than guessed at. A
    templated one -- `==${{ task_sdk_version }}` -- follows the same context
    variable the source URL does and cannot disagree with it by construction,
    which is the shape this check exists to protect and the reason a recipe
    written that way never reaches here.
    """
    built: dict[str, tuple[str, str]] = {}
    for output in recipe.outputs:
        name = output.name
        if name is None:
            continue
        release = upstream.for_output(name)
        if release.version and normalize_name(release.name) == normalize_name(name):
            built[normalize_name(name)] = (name, release.version)

    labels = {
        block.path: output.label
        for output in recipe.outputs
        for block in output.blocks.values()
    }
    found: list[SelfConflict] = []
    for section in sections:
        if section.section != "run":
            continue
        # The package rather than the path: G14's detail is published in the
        # comment swage leaves on the feedstock's pull request, and
        # `/outputs/3` there names nothing anybody can look up.
        where = labels.get(section.path, "")
        for requirement in section.requirements:
            line = parse_line(requirement.text)
            match = built.get(normalize_name(line.name))
            if match is None:
                continue
            package, version = match
            if not _admits(line.constraint, version):
                found.append(
                    SelfConflict(
                        output=where,
                        package=package,
                        constraint=line.constraint,
                        built=version,
                    )
                )
    return tuple(found)


def _admits(constraint: str, version: str) -> bool:
    """Whether a conda constraint is satisfied by ``version``.

    True wherever the question cannot be answered -- a template, a build
    string, a spelling `packaging` will not parse. A check that cannot read a
    constraint has found nothing, and reporting one anyway would be swage
    asserting a conflict it did not establish.
    """
    if not constraint or "${{" in constraint:
        return True
    try:
        return Version(version) in SpecifierSet(constraint.replace(" ", ""))
    except (InvalidSpecifier, InvalidVersion):
        return True


def output_selections(config: FeedstockConfig) -> dict[str, dict[str, frozenset[str]]]:
    """Output name -> extra -> the packages of it that output takes.

    Only extras a feedstock splits across outputs appear here. An extra folded
    in whole says nothing, which is what the empty mapping means downstream:
    take all of it (DESIGN.md 4).
    """
    return {
        name: {
            extra: frozenset(normalize_name(package) for package in packages)
            for extra, packages in output.run.from_extras.items()
        }
        for name, output in config.outputs.items()
        if output.run.from_extras
    }


def plan_recipe(
    recipe: Recipe,
    upstream: RecipeUpstream,
    config: FeedstockConfig,
    resolver: NameResolver,
    python_min: PythonMin | None,
    previous: RecipeUpstream | None = None,
    outputs: Mapping[str, tuple[tuple[str, ...], bool]] | None = None,
    pythons: Sequence[int] = (),
    platforms: Sequence[str] = (),
    pinned: Container[str] = frozenset(),
) -> RecipePlan:
    """Plan every section of every output.

    ``outputs`` overrides what each output draws on; where it says nothing, the
    roles come from config via `output_roles`.

    ``python_min`` is None where neither the recipe nor `.ci_support` declares
    one, which is conda-smithy's answer for a feedstock building no noarch
    python package. The demand for it is made per output below, because that is
    the only place it is known whether one was needed (DESIGN.md 3.3.3).

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

    ``upstream`` is a set of releases rather than one, because a recipe may
    build several and an output reconciles against its own (DESIGN.md 3.6).
    For the recipes that build one -- all but four of the fleet -- every
    output is handed the same release and nothing below can tell the
    difference.
    """
    roles = dict(output_roles(recipe, config))
    roles.update(outputs or {})
    selections = output_selections(config)

    sections: list[PlannedSection] = []
    for output in recipe.outputs:
        listed, core = roles.get(output.name or "", ((), True))
        release = upstream.for_output(output.name or "")
        # The build model, per output, because that is what it is a property of
        # (DESIGN.md, "The build model is a property of each output"):
        # `sqlalchemy` is a compiled base output beside noarch metapackages and
        # `apache-beam` is a compiled base output beside eleven noarch ones.
        noarch = output.noarch == "python"
        if noarch and python_min is not None:
            # Both numbers are in hand exactly here, which is why the check
            # lives here rather than in config (DESIGN.md 4.1).
            check_upstream_floor(output, release.requires_python, python_min)
        # Per output too, because the cap is stated on that output's own
        # `python` line and a split recipe may cap one package and not another.
        python_max = python_ceiling(output)
        for name in PLANNED_SECTIONS:
            block = output.blocks.get(name)
            if block is None:
                continue
            sections.append(
                plan_section(
                    block,
                    release,
                    config,
                    resolver,
                    python_min,
                    listed_extras=listed,
                    core=core,
                    output=output.name or "",
                    label=output.label,
                    from_extras=selections.get(output.name or ""),
                    previous=(
                        None
                        if previous is None
                        else previous.for_output(output.name or "")
                    ),
                    python_max=python_max,
                    noarch=noarch,
                    pythons=pythons,
                    platforms=platforms,
                    pinned=pinned,
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

    # From the *resolved* roles, not from the `outputs` argument. Reading the
    # argument meant reading what a caller passed to override the config, and
    # no caller passes one -- so every extra looked undrawn, including the nine
    # `google-cloud-bigquery` explicitly folds into its metapackage. The bug
    # was invisible on the ~480 feedstocks with no config yet, where "nothing
    # accounts for this extra" is the true answer and the intended starting
    # state; it showed only on the feedstocks where the work had been done.
    drawn = {extra for listed, _ in roles.values() for extra in listed}
    accounted = drawn | accounted_extras(config)
    mirrored, in_step = _mirrors(recipe, sections)
    return RecipePlan(
        sections=(*sections, *mirrored),
        cross_compiled=_cross_compiled(recipe, sections, config, in_step),
        self_conflicts=_self_conflicts(recipe, upstream, sections),
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
    )
