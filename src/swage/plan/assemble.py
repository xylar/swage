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
from dataclasses import dataclass, field, replace

from packaging.version import Version

from swage.config import AddedRequirement, FeedstockConfig, Layered
from swage.mapping import NameResolver
from swage.recipe import (
    BlockContent,
    Conditional,
    Entry,
    Recipe,
    Requirement,
    RequirementsBlock,
    inline_text,
)
from swage.upstream import UpstreamMetadata, UpstreamRequirement

from .attribute import (
    KEPT_UNEXPLAINED,
    Attribution,
    AttributionIndex,
    Provenance,
    Unexplained,
    attribute,
    build_index,
)
from .authored import maintainer_comments
from .constrained import UnassociatedConstraint, check_run_constraints
from .errors import PlanError
from .lines import ParsedLine, parse_line
from .model import PlannedConditional, PlannedEntry, PlannedRequirement
from .order import order_requirements
from .python_min import PythonMin, check_upstream_floor, python_ceiling
from .reconcile import reconcile
from .removals import Removal, classify_removal
from .resolve import resolve_requirement
from .split import Split, split_by_environment
from .test_matrix import TestMatrix, plan_test_matrices
from .tightening import Tightened, tightening

__all__ = [
    "PlannedSection",
    "RecipePlan",
    "output_roles",
    "plan_recipe",
    "plan_section",
    "planned_blocks",
]

#: The only sections swage plans today. `run_constraints` is read, never
#: authored (DESIGN.md 3.3.9). `build` is a longer story: most of it is
#: compilers and cross-compilation helpers that answer no question upstream
#: metadata asks, but a cross-compilation block also repeats `host`'s
#: upstream-derived entries, and what to do about that is open (DESIGN.md
#: 3.3.6.1).
PLANNED_SECTIONS = ("host", "run")


@dataclass(frozen=True)
class PlannedSection:
    """One requirements section as swage would write it."""

    path: str
    section: str
    entries: tuple[PlannedEntry, ...] = ()
    #: Every line the current upstream does not declare, with its fate. Kept
    #: even for lines that stay, because the report explains what was
    #: considered as well as what changed.
    removals: tuple[Removal, ...] = ()
    #: Lines swage could not account for. G1 reads this.
    unexplained: tuple[Unexplained, ...] = ()
    #: Constraints the recipe states more tightly than swage would render.
    #: G11 reads this. A `constraints:` entry is already folded into what swage
    #: renders, so it leaves nothing here rather than being filtered out
    #: (DESIGN.md 3.3.14).
    tightened: tuple[Tightened, ...] = ()
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
    #: `host` sections swage would change on an output that cross-compiles.
    #: G13 reads this (DESIGN.md 3.3.6.1).
    cross_compiled: tuple[str, ...] = field(default=())

    @property
    def unexplained(self) -> tuple[Unexplained, ...]:
        return tuple(u for section in self.sections for u in section.unexplained)

    @property
    def dropped(self) -> tuple[Removal, ...]:
        return tuple(r for section in self.sections for r in section.dropped)

    @property
    def tightened(self) -> tuple[Tightened, ...]:
        return tuple(t for section in self.sections for t in section.tightened)


def _build_floor(block: RequirementsBlock, python_min: PythonMin | None) -> PythonMin:
    """The floor a noarch output collapses its markers over, or the stop.

    Demanded here rather than where it was resolved, because this is where it
    is known that an output needed one (DESIGN.md 3.3.3) -- and demanded
    whether or not upstream declares any dependency this version, so that a
    feedstock conda-smithy has never rendered says so at every version rather
    than only at the ones with a marker in them.
    """
    if python_min is not None:
        return python_min
    where = block.path.rsplit("/requirements/", 1)[0] or "this recipe"
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
    previous: UpstreamMetadata | None = None,
    python_max: Version | None = None,
    noarch: bool = True,
) -> PlannedSection:
    """Plan one requirements section.

    ``noarch`` is the build model of the output this section belongs to, and it
    decides what an upstream environment marker becomes. One noarch package is
    installed on every python from the build floor up, so several
    marker-qualified declarations collapse into the tightest bound that holds
    across the range. An architecture-specific output is built once per python,
    so they become conditions saying what upstream says (DESIGN.md 3.3.1.1).
    """
    if noarch:
        _build_floor(block, python_min)
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
        # Attribution only: `_upstream_groups` below still honours `core`, so
        # an output whose `host` carries no backend does not acquire one.
        # Those two answers differ on purpose -- explaining a line that is
        # there and adding one that is not are different acts, and the fleet's
        # recipes disagree about which outputs build from source
        # (DESIGN.md 3.6.2).
        core=core or block.section == "host",
        section=block.section,
        embedded_extras=config.embedded_extras,
    )
    added = config.add_requirements.get(block.section, ()) + _implicit_backend(
        block.section, upstream, config
    )

    planned: dict[str, PlannedEntry] = {}
    for name, variants, provenance in _upstream_groups(
        upstream, listed_extras, resolver, block.section, core, config.embedded_extras
    ):
        constraint = config.constraints.get(name)
        if noarch:
            result = reconcile(
                name,
                variants,
                _build_floor(block, python_min),
                config.feedstock,
                python_max,
                constraint=constraint,
            )
            considered: Sequence[UpstreamRequirement] = result.considered
        else:
            split = split_by_environment(name, variants, constraint=constraint)
            considered = split.considered
        if not considered:
            # Every declaration is gated on a python this output does not
            # build, so upstream does not ask for this package here at all.
            # Dropping it is a removal decision the planner makes below, not
            # one to make here.
            continue
        # The noarch note names the marker behind the binding bound, because a
        # single artifact had to pick one and the reader is owed why. Nothing
        # was picked on the other path, so nothing is said (DESIGN.md 3.3.1.1).
        comments = _settled_captions(variants, config)
        if noarch:
            comments = ((f"# {result.note}",) if result.note else ()) + comments
            planned[name] = PlannedRequirement(
                _requirement_text(name, result.specifier), provenance, comments
            )
        else:
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

    for addition in added:
        planned.setdefault(
            parse_line(addition.text).name,
            PlannedRequirement(
                parse_line(addition.text).rendered,
                Provenance("config-add", addition.source),
            ),
        )

    removals: list[Removal] = []
    unexplained: list[Unexplained] = []
    tightened: list[Tightened] = []
    previous_index = (
        build_index(previous, listed_extras, resolver, core=core, section=block.section)
        if previous is not None
        else None
    )

    preserved: dict[str, tuple[str, ...]] = {}
    for position, entry in enumerate(block.content.entries):
        if isinstance(entry, Conditional):
            key, kept, unaccounted = _existing_conditional(
                entry, position, block, planned, index, config, added
            )
            preserved[key] = maintainer_comments(entry.comments)
            unexplained.extend(unaccounted)
            if kept is not None:
                planned[key] = kept
            continue

        requirement = entry
        line = parse_line(entry.text)
        explanation = attribute(line, index, config.recipe_owned, added)
        pending = explanation if isinstance(explanation, Unexplained) else None
        key = _planned_key(line, explanation)
        # Last of several lines mapping to one planned line, not first. Two
        # recipe lines collapse into one wherever an `embedded_extras`
        # expansion repeats a dependency upstream also declares -- and the
        # comments above the *first* of those are about the expansion, which
        # swage now delimits with its own markers, so carrying them to a line
        # further down the section re-anchors a remark to something it was
        # never about.
        preserved[key] = maintainer_comments(requirement.comments)

        if key in planned:
            # Upstream still asks for it; the reconciled line replaces this
            # one -- so this is the only place a bound the recipe states and
            # the plan does not can be noticed at all (DESIGN.md 3.3.14).
            if pending is not None:
                unexplained.append(pending)
            replacement = planned[key]
            # Only against a plain line. Where the plan states the dependency
            # per python range, "the bound the recipe has and the plan does
            # not" has an answer per range rather than one answer, and G11
            # reports a bound the maintainer would be asked to defend.
            if isinstance(replacement, PlannedRequirement):
                lost = tightening(
                    key, line.constraint, parse_line(replacement.text).constraint
                )
                if lost is not None:
                    tightened.append(lost)
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
    entries, trailing = _with_expansion_markers(annotated)
    return PlannedSection(
        path=block.path,
        section=block.section,
        entries=entries,
        removals=tuple(removals),
        unexplained=tuple(unexplained),
        tightened=tuple(tightened),
        trailing_comments=trailing,
    )


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
) -> tuple[str, PlannedEntry | None, tuple[Unexplained, ...]]:
    """What becomes of a conditional entry the recipe already has.

    Three outcomes, and which one applies is decided by what the plan says
    about the dependencies *inside* the entry.

    **Replaced**, where swage plans one of them conditionally: it derived the
    same structure from upstream's markers, so what it writes supersedes what
    is there. That is the ordinary case on a recipe swage has written before,
    and the reason a second run is a no-op.

    **Refused**, where swage plans one of them as a plain line. Somebody
    conditioned this dependency and upstream's metadata does not say why --
    rendering the plan would delete the condition, which is a packaging
    decision rather than a reconciliation (DESIGN.md 3.3.4).

    **Preserved** otherwise, exactly as read, so it renders back byte for byte.
    Its dependencies are still attributed: a conditional entry nothing explains
    fails G1 like any other line, and the feedstock is held for a human rather
    than merged. It is never *removed* -- swage does not delete a structure it
    did not author on evidence about one of the names inside it.

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
        _planned_key(line, explanation)
        for line, explanation in zip(lines, explanations, strict=True)
    ]

    for key in keys:
        replacement = planned.get(key)
        if replacement is None:
            continue
        if isinstance(replacement, PlannedRequirement):
            raise PlanError(_condition_would_be_lost(block, entry, replacement))
        return key, None, ()

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
    )


#: How a preserved conditional is keyed in the planned section. Not a name:
#: what it is keyed by is where it was.
_CONDITIONAL = "conditional:"


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
    block: RequirementsBlock, entry: Conditional, replacement: PlannedRequirement
) -> str:
    """The message for a condition swage would delete rather than reconcile."""
    return (
        f"cannot plan {block.path}: it states {replacement.name!r} conditionally "
        "and upstream does not\n"
        f"    if: {entry.condition}\n"
        f"  upstream asks for it on every build this output produces, so swage "
        f"would write one unconditional line -- {replacement.text} -- and the "
        "condition would be gone\n"
        "  keeping it is a decision about what the package promises, so swage "
        "makes neither: resolve by hand"
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
            entry = replace(
                entry, comments=(f"# from the {extra} extra", *entry.comments)
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


def _cross_compiled(
    recipe: Recipe, sections: Sequence[PlannedSection]
) -> tuple[str, ...]:
    """`host` sections swage would change on an output that cross-compiles.

    **15 of the 19 outputs in the fleet with a cross-compilation block repeat a
    `host` requirement inside it** -- `cython`, `numpy`, `cffi`, `pybind11`,
    `maturin`, `grpcio-tools` -- because a cross build resolves its build tools
    for the build platform rather than the target. So a `host` requirement swage
    adds or bumps may need mirroring into `build`, and a recipe that got only
    half of that builds natively and fails cross-compiled.

    **What to mirror is a judgement per dependency, not a set operation**:
    `pyproj` mirrors `cython` and not `proj`; `python-eccodes` mirrors `numpy`
    and `cffi` and not `findlibs`. Until there is a rule for it (DESIGN.md
    3.3.6.1), swage plans the `host` change and holds the feedstock for a human
    rather than merging it unattended.

    Changes rather than additions, because a bumped bound needs mirroring
    exactly as much as a new line does.
    """
    changed: list[str] = []
    planned = {section.path: section for section in sections}
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
        if before != after:
            changed.append(host.path)
    return tuple(changed)


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
    python_min: PythonMin | None,
    previous: UpstreamMetadata | None = None,
    outputs: Mapping[str, tuple[tuple[str, ...], bool]] | None = None,
) -> RecipePlan:
    """Plan every section of every output.

    ``outputs`` overrides what each output draws on; where it says nothing, the
    roles come from config via `output_roles`.

    ``python_min`` is None where neither the recipe nor `.ci_support` declares
    one, which is conda-smithy's answer for a feedstock building no noarch
    python package. The demand for it is made per output below, because that is
    the only place it is known whether one was needed (DESIGN.md 3.3.3).
    """
    roles = dict(output_roles(recipe, config))
    roles.update(outputs or {})

    sections: list[PlannedSection] = []
    for output in recipe.outputs:
        listed, core = roles.get(output.name or "", ((), True))
        # The build model, per output, because that is what it is a property of
        # (DESIGN.md, "The build model is a property of each output"):
        # `sqlalchemy` is a compiled base output beside noarch metapackages and
        # `apache-beam` is a compiled base output beside eleven noarch ones.
        noarch = output.noarch == "python"
        if noarch and python_min is not None:
            # Both numbers are in hand exactly here, which is why the check
            # lives here rather than in config (DESIGN.md 4.1).
            check_upstream_floor(output, upstream.requires_python, python_min)
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
                    upstream,
                    config,
                    resolver,
                    python_min,
                    listed_extras=listed,
                    core=core,
                    previous=previous,
                    python_max=python_max,
                    noarch=noarch,
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
        cross_compiled=_cross_compiled(recipe, sections),
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
