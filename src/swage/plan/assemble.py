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

from swage.config import AddedRequirement, FeedstockConfig, Layered
from swage.mapping import NameResolver
from swage.recipe import BlockContent, Recipe, Requirement, RequirementsBlock
from swage.upstream import UpstreamMetadata, UpstreamRequirement

from .attribute import (
    KEPT_UNEXPLAINED,
    Attribution,
    Provenance,
    Unexplained,
    attribute,
    build_index,
)
from .authored import maintainer_comments
from .constrained import UnassociatedConstraint, check_run_constraints
from .lines import ParsedLine, parse_line
from .model import PlannedRequirement
from .order import order_requirements
from .python_min import PythonMin
from .reconcile import reconcile
from .removals import Removal, classify_removal
from .resolve import resolve_requirement

__all__ = [
    "PlannedSection",
    "RecipePlan",
    "output_roles",
    "plan_recipe",
    "plan_section",
    "planned_blocks",
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
    #: Comments after the last requirement and still inside the block. The
    #: `# end` half of an embedded-extras marker pair lands here when the
    #: expansion runs to the end of the section, which is the common case and
    #: has no following requirement to sit above (DESIGN.md 6).
    trailing_comments: tuple[str, ...] = ()

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
    added = config.add_requirements.get(block.section, ()) + _implicit_backend(
        block.section, upstream, config
    )

    planned: dict[str, PlannedRequirement] = {}
    for name, variants, provenance in _upstream_groups(
        upstream, listed_extras, resolver, block.section, core, config.embedded_extras
    ):
        result = reconcile(name, variants, python_min, config.feedstock)
        if not result.considered:
            # Every declaration was gated below the build floor, so upstream
            # does not ask for this package on any Python conda-forge ships.
            continue
        text = f"{name} {result.specifier}" if result.specifier else name
        comments: tuple[str, ...] = (f"# {result.note}",) if result.note else ()
        comments += _settled_captions(variants, config)
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

    preserved: dict[str, tuple[str, ...]] = {}
    for requirement in block.content.requirements:
        line = parse_line(requirement.text)
        explanation = attribute(line, index, config.recipe_owned, added)
        pending = explanation if isinstance(explanation, Unexplained) else None
        key = _planned_key(line, explanation)
        preserved.setdefault(key, maintainer_comments(requirement.comments))

        if key in planned:
            # Upstream still asks for it; the reconciled line replaces this one.
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
    requirements, trailing = _with_expansion_markers(annotated)
    return PlannedSection(
        path=block.path,
        section=block.section,
        requirements=requirements,
        removals=tuple(removals),
        unexplained=tuple(unexplained),
        trailing_comments=trailing,
    )


def _implicit_backend(
    section: str, upstream: UpstreamMetadata, config: FeedstockConfig
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
    """
    if section != "host" or upstream.build_requires is not None:
        return ()
    return tuple(
        AddedRequirement(
            text,
            "config/defaults.yaml: default_build_requires "
            "(upstream declares no build system)",
        )
        for text in config.default_build_requires
    )


def _upstream_groups(
    upstream: UpstreamMetadata,
    listed_extras: Sequence[str],
    resolver: NameResolver,
    section: str,
    core: bool,
    embedded_extras: Layered[tuple[str, ...]] | None = None,
) -> list[tuple[str, list[UpstreamRequirement], Provenance]]:
    """Group upstream's requirements by the conda name they resolve to.

    Core wins over an extra where a package appears in both, matching
    attribution's order (DESIGN.md 3.3.10) so a line's provenance does not
    depend on which code looked at it.
    """
    groups: dict[str, list[UpstreamRequirement]] = {}
    provenance: dict[str, Provenance] = {}

    def add(requirement: UpstreamRequirement, origin: Provenance) -> None:
        resolution = resolve_requirement(requirement, resolver, embedded_extras)
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


def _planned_key(line: ParsedLine, explanation: Attribution) -> str:
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
    """
    if isinstance(explanation, Provenance) and explanation.mapping is not None:
        return explanation.mapping.conda_name
    return line.name


def _with_preserved_comments(
    planned: dict[str, PlannedRequirement],
    preserved: Mapping[str, tuple[str, ...]],
) -> dict[str, PlannedRequirement]:
    """Carry each requirement's maintainer-written comments onto its new line.

    DESIGN.md 6.1: a requirement's rendered comments are the ones swage
    generates this run, then every comment the recipe had above it that swage
    did not write. Applied here, once, rather than at each of the four places a
    `PlannedRequirement` is built -- an upstream line, an `embedded_extras`
    expansion, an `add_requirements` entry and a kept line -- because a rule
    about what a section looks like should not depend on which branch produced
    the line.

    Doing it per branch is what the previous behaviour amounted to, and it was
    not merely lossy but inconsistent: a kept line carried `requirement.comments`
    through untouched while an upstream line had them replaced by whatever was
    generated for it, usually nothing. So a note survived above a dependency
    swage could not explain and was destroyed above one it could --
    `google-cloud-bigquery`'s note about `google-auth[pyopenssl]` being of the
    second kind.

    Generated comments come first because they are structural: a block header
    partitions the section and has to lead it, and swage's own note about the
    line reads as a caption above the maintainer's remark rather than below it.

    ``preserved`` is keyed by the name the *plan* renders each recipe line
    under (`_planned_key`) rather than by the line's own spelling, and is built
    by the caller as it attributes each line. Recomputing it here from the
    block was a second, subtly different answer to "which planned line is this
    recipe line": a note above `pyOpenSSL` belongs to whatever swage renders
    for that requirement, including when it renders it as `pyopenssl`.
    """
    return {
        name: PlannedRequirement(
            entry.text,
            entry.provenance,
            (*entry.comments, *preserved.get(name, ())),
        )
        for name, entry in planned.items()
    }


def _with_extra_headers(
    ordered: Sequence[PlannedRequirement],
    listed_extras: Sequence[str],
    core: bool,
) -> tuple[PlannedRequirement, ...]:
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


#: How `_expansions` labels a line it spliced in, which is what lets the
#: markers below find those lines again after ordering has moved them.
_EMBEDDED = "embedded_extras:"


def _embedded_key(entry: PlannedRequirement) -> str | None:
    """The `name[extra]` an expansion line stands in for, or None."""
    provenance = entry.provenance
    if provenance.origin != "config-add" or not provenance.detail.startswith(_EMBEDDED):
        return None
    return provenance.detail[len(_EMBEDDED) :].partition(" (")[0]


def _with_expansion_markers(
    ordered: Sequence[PlannedRequirement],
) -> tuple[tuple[PlannedRequirement, ...], tuple[str, ...]]:
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
    result: list[PlannedRequirement] = []
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
            PlannedRequirement(entry.text, entry.provenance, (*before, *entry.comments))
            if before
            else entry
        )
    trailing = (f"# end {open_key}",) if open_key is not None else ()
    return tuple(result), trailing


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
            tuple(
                Requirement(requirement.text, requirement.comments)
                for requirement in section.requirements
            ),
            section.trailing_comments,
        )
        for section in plan.sections
    }


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
        accounted |= set(output.run.extras) | set(output.run.skip)
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

    # From the *resolved* roles, not from the `outputs` argument. Reading the
    # argument meant reading what a caller passed to override the config, and
    # no caller passes one -- so every extra looked undrawn, including the nine
    # `google-cloud-bigquery` explicitly folds into its metapackage. The bug
    # was invisible on the ~480 feedstocks with no config yet, where "nothing
    # accounts for this extra" is the true answer and the intended starting
    # state; it showed only on the feedstocks where the work had been done.
    drawn = {extra for listed, _ in roles.values() for extra in listed}
    accounted = drawn | accounted_extras(config)
    return RecipePlan(
        sections=tuple(sections),
        unassociated_constraints=check_run_constraints(
            constrained, config.run_constraints
        ),
        python_min=python_min,
        unaccounted_extras=tuple(
            extra for extra in upstream.extras if extra not in accounted
        ),
    )
