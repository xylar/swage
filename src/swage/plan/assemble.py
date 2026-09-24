"""Turn a recipe, its upstream metadata and the quirks database into a plan.

`plan_section` is the core computation (DESIGN.md §9): `Output` says how the
section's output is built, `reconcile` collapses a package's marker variants
over that output's grid, `attribute` explains what is already in the recipe,
`classify_removal` decides what may leave, and `order_requirements` puts the
result in the order swage writes. Upstream's lines are computed first and the
recipe's folded in after, so a line upstream still declares is replaced and one
it does not survives only by §9.5's rules. Nothing here authors a
`run_constraints` entry (v1 §3.3.9).
"""

from __future__ import annotations

from collections.abc import Container, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from swage.config import (
    AddedRequirement,
    FeedstockConfig,
    Layered,
    NotPackaged,
    VariantCondition,
)
from swage.mapping import NameResolver, normalize_name
from swage.recipe import (
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
from .errors import PlanError
from .grid import Reconciled, parse_marker, reconcile, settled_already
from .lines import ParsedLine, parse_line, spec_key
from .markers import summarize_python
from .model import PlannedConditional, PlannedEntry, PlannedRequirement
from .order import order_requirements
from .output import Output
from .prose import section_phrase
from .removals import Removal, classify_removal
from .resolve import resolve_requirement

__all__ = [
    "AppliedOverride",
    "PlannedSection",
    "SelfConflict",
    "accounted_extras",
    "cross_compiled_hosts",
    "declares_skip",
    "mirrored_sections",
    "plan_section",
    "self_conflicts",
]


@dataclass(frozen=True)
class AppliedOverride:
    """A bound from config that this section applied, and the dependency it
    bounds: `Override` is keyed by that name in config and does not carry it.
    """

    name: str
    bound: str
    reason: str

    @property
    def text(self) -> str:
        """The line as the recipe states it, less whatever upstream adds."""
        return f"{self.name} {self.bound}"


@dataclass(frozen=True)
class SelfConflict:
    """A requirement on a package this recipe builds, at a version it does not.

    Only a split recipe can produce one. Where `source_versions` lets swage
    correct `context`, this reports what could not be corrected; where it does
    not, this is the whole answer (v1 §3.6.5; DESIGN.md §9.7).
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

    #: Where the block is in the parsed document; the writer splices by it.
    path: str
    section: str
    #: The same section, said the way the recipe's maintainer would say it.
    where: str = ""
    entries: tuple[PlannedEntry, ...] = ()
    #: Every line the current upstream does not declare, with its fate.
    removals: tuple[Removal, ...] = ()
    #: Lines swage could not account for. G1 reads this.
    unexplained: tuple[Unexplained, ...] = ()
    #: Temporary overrides this section applied, re-asked at every update (G11).
    overrides: tuple[AppliedOverride, ...] = ()
    #: Overruling bounds this section applied (v1 §3.3.2), re-asked at every
    #: update (G11). Apart from `overrides` because the question asked differs.
    overruled: tuple[AppliedOverride, ...] = ()
    #: Temporary `add_requirements` entries this section carries (G11). Carries,
    #: not declares: an entry explaining a conditional line adds nothing.
    temporary_additions: tuple[AddedRequirement, ...] = ()
    #: Comments after the last requirement and still inside the block, where the
    #: `# end` half of an expansion's marker pair lands (v1 §6).
    trailing_comments: tuple[str, ...] = ()

    @property
    def requirements(self) -> tuple[PlannedRequirement, ...]:
        """The unconditional entries, in order.

        Not everything the section holds, as with `BlockContent.requirements`:
        a python-gated dependency on a per-python output is a condition.
        Anything deciding what swage writes reads `entries`.
        """
        return tuple(
            entry for entry in self.entries if isinstance(entry, PlannedRequirement)
        )

    @property
    def dropped(self) -> tuple[Removal, ...]:
        return tuple(removal for removal in self.removals if removal.removed)


def _declared_for(variants: Sequence[UpstreamRequirement], name: str) -> str:
    """Which pythons upstream's declarations of ``name`` are gated on.

    Called only for a package every declaration of which reaches outside the
    range this output covers, so each carries a marker.
    """
    windows = []
    for variant in variants:
        marker = parse_marker(variant, name)
        window = str(variant.marker) if marker is None else summarize_python(marker)
        if window not in windows:
            windows.append(window)
    return " or ".join(windows)


def plan_section(
    block: RequirementsBlock,
    upstream: UpstreamMetadata,
    config: FeedstockConfig,
    resolver: NameResolver,
    output: Output,
    previous: UpstreamMetadata | None = None,
    context: Mapping[str, str] = MappingProxyType({}),
) -> PlannedSection:
    """Plan one requirements section (DESIGN.md §9.3).

    ``output`` is the one this section belongs to; its build model decides
    what an upstream environment marker becomes (§9.1). ``output.package`` is
    what an `add_requirements` entry naming one output matches; None for a
    recipe with no `outputs` list. ``context`` is the recipe's, for reading a
    template where swage would write the literal it resolves to.
    """
    universe = output.universe
    package = output.package or ""
    index = build_index(
        upstream,
        output.extras,
        resolver,
        # `core` decides what `run` draws, not what `host` explains (DESIGN.md
        # §9.1): `_upstream_groups` honors it for both, attribution for `run`
        # only.
        core=output.core or block.section == "host",
        section=block.section,
        output=output.name,
        embedded_extras=config.embedded_extras,
        from_extras=output.from_extras,
    )
    added = config.add_requirements.get(block.section, package) + _implicit_backend(
        block.section, upstream, config, output.core
    )

    planned: dict[str, PlannedEntry] = {}
    applied: list[AppliedOverride] = []
    settled: list[AppliedOverride] = []
    settled_names: set[str] = set()
    out_of_range: dict[str, str] = {}
    for name, variants, provenance in _upstream_groups(
        upstream,
        output.extras,
        resolver,
        block.section,
        output.core,
        config.embedded_extras,
        output.from_extras,
    ):
        absent = _not_packaged(name, variants, config)
        if absent is not None:
            if provenance.mapping is not None:
                # The entry is the only thing keeping this dependency out, so it
                # cannot outlive conda-forge packaging the thing (v1 §3.2.3).
                raise PlanError(
                    _now_packaged(name, provenance.mapping.conda_name, config)
                )
            continue
        # Permanent and temporary overrides render alike; `applied` is what G11
        # asks about again.
        override = config.constraints.get(name) or config.temporary_constraints.get(
            name
        )
        constraint = override.bound if override is not None else None
        if name in config.temporary_constraints:
            temporary = config.temporary_constraints[name]
            applied.append(AppliedOverride(name, temporary.bound, temporary.reason))
        # Stands in for upstream's declarations rather than narrowing them (v1
        # §3.3.2).
        overruled = config.overruled_constraints.get(name)
        # Read on every path, not only the ones that stop: one declaration can
        # reach a compiled output and a noarch one alike (v1 §3.3.4.1).
        result = reconcile(
            name,
            variants,
            universe,
            feedstock=config.feedstock,
            constraint=constraint,
            built_everywhere=name in config.built_everywhere,
            overruled=None if overruled is None else overruled.bound,
            output=output.name,
        )
        note = result.note
        considered = result.considered
        if result.overruled and overruled is not None:
            settled.append(AppliedOverride(name, overruled.bound, overruled.reason))
            settled_names.add(name)
        if not considered:
            # Every declaration is gated on a python this output does not build:
            # nothing is planned, and the removal loop below drops the recipe's
            # line.
            out_of_range[name] = _declared_for(variants, name)
            continue
        if overruled is not None and name not in settled_names:
            # Checked here rather than in `reconcile`, which runs once per
            # platform; one platform needing the entry is enough (v1 §3.3.2).
            raise PlanError(settled_already(name, config.feedstock))
        # The collapse note names the marker behind the binding bound (DESIGN.md
        # §9.3 step 8).
        comments = _settled_captions(variants, config)
        as_written = _unversioned_reader_line(block, upstream, name, variants)
        if as_written is not None:
            # A build system names packages, not versions: the recipe's bound
            # stands (v1 §3.6.6, §3.6.7).
            planned[name] = PlannedRequirement(as_written, provenance, comments)
        elif block.section == "host" and name in output.pinned:
            # The pinning states the version, so no bound and no note (DESIGN.md
            # §9.4).
            planned[name] = PlannedRequirement(name, provenance, comments)
        else:
            # A note survives only where every artifact agreed on a plain line
            # (DESIGN.md §9.3 step 8).
            comments = ((f"# {note}",) if note else ()) + comments
            planned[name] = _planned_entry(name, result, provenance, comments)
        for expansion, detail, source in _expansions(variants, config):
            expanded = parse_line(expansion)
            planned.setdefault(
                expanded.name,
                PlannedRequirement(
                    expanded.rendered,
                    Provenance("config-add", f"{detail} ({source})"),
                ),
            )

    # Names this section already states inside a condition: an entry for one
    # explains that line rather than adding an unconditional one (v1 §3.3.4).
    conditional = _conditionally_stated(block)
    carried: list[AddedRequirement] = []
    for addition in added:
        # Keyed as a recipe line is (`_planned_key`), build string included, so
        # an entry carrying one files under the line it explains.
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
    # Once, and only where something needs it: on a section with nothing out of
    # range this is a sentence nobody reads.
    built_for = output.built_for if out_of_range else ""
    previous_index = (
        build_index(
            previous,
            output.extras,
            resolver,
            core=output.core,
            section=block.section,
            output=output.name,
            from_extras=output.from_extras,
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
                output.pinned,
                output.name,
                settled_names,
            )
            if retired is not None:
                # Config named every dependency inside, so the entry goes as a
                # retired plain line does, and is not also reported to G1.
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
        key = _planned_key(line, explanation, block.section, output.pinned)

        if line.platform_expansions and isinstance(explanation, Provenance):
            # The `noarch_platform` idiom is read, kept as written and never
            # rewritten (DESIGN.md §9.4); the plan's own copy is dropped.
            for expansion in line.platform_expansions:
                if not parse_line(expansion).recipe_owned(config.recipe_owned):
                    planned.pop(expansion, None)
            preserved[key] = maintainer_comments(requirement.comments)
            planned[key] = PlannedRequirement(entry.text, explanation)
            continue
        # Last of several lines mapping to one planned line: the comments above
        # the first are about an expansion swage now delimits itself.
        preserved[key] = maintainer_comments(requirement.comments)

        if key in planned:
            # Upstream still asks for it, so the reconciled line replaces this
            # one, constraint and all (v1 §3.3.14), unless the recipe wrote the
            # same bound as a template.
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
            out_of_range=out_of_range,
            built_for=built_for,
        )
        removals.append(removal)
        # A retired line is accounted for and removed, not also reported to G1.
        # Every other unexplained line is reported, dropped or not.
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
    annotated = _with_extra_headers(ordered, output.extras, output.core)
    entries, generated = _with_expansion_markers(annotated)
    # A trailing remark has no requirement below it to be anchored to (v1 §6.1),
    # so it is carried explicitly.
    trailing = _in_reading_order(
        generated, maintainer_comments(block.content.trailing_comments)
    )
    return PlannedSection(
        path=block.path,
        section=block.section,
        where=section_phrase(block.section, output.name),
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

    An `add_requirements` entry explains a conditional line rather than adding
    a plain one beside it (DESIGN.md §9.4). Names rather than keys, and every
    branch rather than the taken one.
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

    A template survives exactly where it resolves, through the recipe's own
    context, to the line swage was about to write (DESIGN.md §9.4). Anything
    else is drift and is reconciled.
    """
    if "${{" not in text or not isinstance(planned, PlannedRequirement):
        return None
    resolved = resolve_expression(text, context)
    if resolved is None or not _same_requirement(resolved, planned.text):
        return None
    return replace(planned, text=text)


def _same_requirement(one: str, other: str) -> bool:
    """Whether two requirement lines say the same thing, whitespace aside."""
    return "".join(one.split()) == "".join(other.split())


def _unversioned_reader_line(
    block: RequirementsBlock,
    upstream: UpstreamMetadata,
    name: str,
    variants: Sequence[UpstreamRequirement],
) -> str | None:
    """The recipe's own line, where upstream has no version to reconcile.

    Reached only for a reader with `states_versions: false` and a declaration
    carrying no specifier (DESIGN.md §9.4). Returned as the recipe wrote it,
    template and all, keyed by `spec_key`. None where the recipe does not
    state the package.
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
    """What becomes of a conditional entry the recipe already has (DESIGN.md
    §9.4).

        Replaced, where swage plans one of its dependencies conditionally.
        Refused, where swage plans one as a plain line, unless ``overruled``
        already carries the decision. Retired, where `retire` covers every
        dependency inside and upstream declares none of them here. Preserved
        otherwise, byte for byte, its dependencies still attributed.

        The key is the planned name where the entry is replaced, and its position
        in the section otherwise, because several preserved conditionals can name
        the same package first.
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
                # An `overruled_constraints` entry is the decision this
                # condition recorded (v1 §3.3.2); refusing would ask about it
                # again.
                return key, None, (), None
            blessed = _blessed_variant(entry, replacement.name, config)
            if blessed is None:
                raise PlanError(
                    _condition_would_be_lost(block, entry, replacement, config, label)
                )
            # A blessed build variant: upstream's unconditional declaration
            # explains this line (v1 §3.3.4). Keyed on the planned name so it
            # takes that slot.
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

    Both halves, so a condition blesses only the packages it names (v1
    §3.3.4). Whitespace in the condition is normalized; nothing else is.
    """
    written = _normalized_condition(entry.condition)
    for blessed in config.variant_conditions:
        if _normalized_condition(blessed.condition) == written and blessed.covers(
            package
        ):
            return blessed
    return None


def _under_variant(explanation: Attribution, blessed: VariantCondition) -> Provenance:
    """The provenance for a line kept inside a blessed build variant: upstream's
    dependency, under a condition config accounts for.
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

    The three tests are `classify_removal`'s own, in its order.
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
    """Every requirement a section states, conditional branches included."""
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

    Where nothing blesses the condition, the remedy is `variant_conditions`;
    where something blesses it for other packages, the remedy is adding this
    one to the list.
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
            f"cannot plan {where}: {replacement.name!r} is under a condition "
            "explained elsewhere\n"
            f"    if: {entry.condition}\n"
            f"  config accounts for this condition around "
            f"{', '.join(blessed.packages)}, and upstream asks for "
            f"{replacement.name!r} on every build this output produces\n"
            f"  add {replacement.name!r} to that entry's `packages` if it "
            "belongs there too, or move the line out of the condition"
        )
    return (
        f"cannot plan {where}: {replacement.name!r} is conditional and "
        "upstream's is not\n"
        f"    if: {entry.condition}\n"
        f"  upstream asks for it on every build this output produces, so swage "
        f"would write one unconditional line -- {replacement.text} -- and the "
        "condition would be gone\n"
        "  keeping it is a decision about what the package promises, so swage "
        "makes neither: resolve by hand, or say the condition selects a build "
        "variant with `variant_conditions` in config"
    )


def _planned_entry(
    name: str, result: Reconciled, provenance: Provenance, comments: tuple[str, ...]
) -> PlannedEntry:
    """Render one dependency's python ranges as the entries a section holds.

    A plain line, one entry with an `else:`, or one entry per range
    (DESIGN.md §9.3 step 10).
    """
    if len(result.entries) == 1 and result.entries[0].condition is None:
        return PlannedRequirement(
            _requirement_text(name, result.entries[0].specifier), provenance, comments
        )
    if result.complementary:
        first, second = result.entries
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
            for branch in result.entries
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

    `build_requires is None` is upstream saying nothing, which PEP 517 reads
    as the legacy setuptools backend; an empty tuple is upstream saying it
    needs nothing (v1 §3.6.2). Routed through `add_requirements` so the line
    carries a provenance. A metapackage output (`core: false`) gets nothing.
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

    Looked up by upstream's names as well as the group's key, because an
    unresolved name is grouped under upstream's spelling.
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
    attribution's order (DESIGN.md §9.5).
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
            # A group can hold a plain requirement and one carrying an
            # unaccounted extra; both provenances are kept so G2 has something
            # to stop on, and the origin is the first's.
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

    An absent `embedded_extras` entry is a question G2 stops on; an empty one
    is a decision, and earns the caption (v1 §3.6.2, §6). Deduplicated on the
    key.
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
    """The name the plan renders this recipe line under (DESIGN.md §9.4).

    The resolution that attributed the line says where it belongs; without
    one the line's own name is the key. A build string is part of the key,
    and so is a constraint on a `host` line naming a package the pinning
    covers, where there is no build string.
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

    Generated comments first, then every comment the recipe had above the
    requirement (v1 §6.1), applied once here rather than where each
    `PlannedRequirement` is built. Blank lines are spacing, not comments, and
    stay above everything. ``preserved`` is keyed by `_planned_key`.
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
    """Introduce each extra's dependencies with a header naming it (v1 §6).

    Only where the section holds lines from more than one source; an output
    drawing one extra and no core says which in its name.
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
            # Below any blank lines the recipe had, which are spacing above the
            # whole group.
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
    """Wrap each embedded-extras expansion in its `# start`/`# end` pair (v1
    §6).

        `# start` sits above the first expanded line; `# end` becomes the leading
        comment of what follows, or the section's trailing comment.
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


#: The `recipe-kept` detail on a cross-compiled `build` line swage carries
#: through untouched, and on the copy it keeps in step.
_BUILD_CARRIED = "the build section, as the recipe has it"
_BUILD_MIRRORED = "kept in step with the host requirement it copies"


def mirrored_sections(
    recipe: Recipe, outputs: Sequence[Output], sections: Sequence[PlannedSection]
) -> tuple[tuple[PlannedSection, ...], Mapping[str, frozenset[str]]]:
    """`build` sections whose copy of a `host` line swage is keeping in step.

    Only where the copy currently agrees with its `host` line, and only its
    constraint, never the line (v1 §3.3.6.1). Returns the sections to write
    and, per `host` block, the names whose copy was kept in step.
    """
    planned = {section.path: section for section in sections}
    written: list[PlannedSection] = []
    in_step: dict[str, frozenset[str]] = {}
    for recipe_output, output in zip(recipe.outputs, outputs, strict=True):
        if not output.cross_compiled:
            continue
        build = recipe_output.blocks["build"]
        host = recipe_output.blocks.get("host")
        if host is None:
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
                where=section_phrase(build.section, output.name),
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


def cross_compiled_hosts(
    recipe: Recipe,
    outputs: Sequence[Output],
    sections: Sequence[PlannedSection],
    config: FeedstockConfig,
    in_step: Mapping[str, frozenset[str]],
) -> tuple[str, ...]:
    """The outputs that cross-compile and whose `host` section swage would
    change.

        A changed `host` requirement may need mirroring into the cross-compilation
        block, which is a judgment swage does not make (v1 §3.3.6.1), so the
        `cross-build-copy` finding holds the feedstock (DESIGN.md §9.7). Names
        `mirrored_sections` kept in step, names `pure_python_build_tools` covers,
        and reorderings ask nothing.
    """
    changed: list[str] = []
    planned = {section.path: section for section in sections}
    exempt = frozenset(normalize_name(name) for name in config.pure_python_build_tools)
    for recipe_output, output in zip(recipe.outputs, outputs, strict=True):
        if not output.cross_compiled:
            continue
        build = recipe_output.blocks["build"]
        host = recipe_output.blocks.get("host")
        if host is None:
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
        # A name gone from `host` that the block does not repeat leaves nothing
        # to go stale.
        gone = _named(before) - _named(after)
        moved = (
            _named(set(before) ^ set(after))
            - in_step.get(host.path, frozenset())
            - (gone - repeated)
        )
        if all(name in exempt and name not in repeated for name in moved):
            continue
        changed.append(output.name)
    return tuple(changed)


def _named(texts: Iterable[str]) -> set[str]:
    """The packages a set of requirement lines names."""
    return {normalize_name(parse_line(text).name) for text in texts}


def _texts(entry: PlannedEntry) -> list[str]:
    """One planned entry as the lines it writes, without comments."""
    if isinstance(entry, PlannedRequirement):
        return [entry.text]
    return [inline_text(conditional) for conditional in entry.conditionals]


def accounted_extras(config: FeedstockConfig) -> set[str]:
    """Upstream extras this feedstock's config says something about.

    `skip` accounts for an extra as firmly as `supported` (v1 §4). One
    definition, read by G3 and by the plan's report. `embedded_extras` names a
    dependency's extras, not this project's, and says nothing here.
    """
    accounted: set[str] = set()
    extras_as_outputs = config.extras_as_outputs
    if extras_as_outputs is not None:
        accounted |= set(extras_as_outputs.supported) | set(extras_as_outputs.skip)
    for output in config.outputs.values():
        # `from_extras` accounts for an extra as firmly as `extras` does.
        accounted |= (
            set(output.run.extras) | set(output.run.skip) | set(output.run.from_extras)
        )
    return accounted


def declares_skip(config: FeedstockConfig) -> bool:
    """Whether this feedstock opted into exhaustiveness (G3, v1 §4), through
    either `extras_as_outputs.skip` or `outputs[].run.skip`.
    """
    extras_as_outputs = config.extras_as_outputs
    if extras_as_outputs is not None and extras_as_outputs.skip:
        return True
    return any(output.run.skip for output in config.outputs.values())


def self_conflicts(
    recipe: Recipe,
    upstream: RecipeUpstream,
    sections: Sequence[PlannedSection],
) -> tuple[SelfConflict, ...]:
    """Requirements on a package this recipe builds, at a version it does not.

    What the recipe builds is what its archives are pinned at (v1 §3.6), so
    this asks `RecipeUpstream` rather than `context`. An unevaluable
    constraint is passed over.
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
        # The package rather than the path: the detail is published on the pull
        # request.
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

    True wherever the question cannot be answered: a check that cannot read a
    constraint has found nothing.
    """
    if not constraint or "${{" in constraint:
        return True
    try:
        return Version(version) in SpecifierSet(constraint.replace(" ", ""))
    except (InvalidSpecifier, InvalidVersion):
        return True
