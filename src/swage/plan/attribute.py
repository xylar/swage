"""Explain every requirement already in a recipe, or refuse to (DESIGN.md §9.5).

Attribution runs in a fixed order: recipe-owned, upstream core, a listed extra,
an unlisted extra (a finding naming the extra), `add_requirements`, nowhere (a
finding whose remedy is `add_requirements` or dropping the line). `Provenance`
is how G1 is checked rather than assumed. The two failures have opposite
remedies, which is why they are told apart (v1 §3.3.10).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, TypeVar

from swage.config import AddedRequirement, Layered, RecipeOwned
from swage.mapping import NameResolver, Resolution, normalize_name
from swage.upstream import UpstreamMetadata, UpstreamRequirement

from .lines import ParsedLine, parse_line
from .prose import fenced, section_phrase
from .resolve import resolve_requirement

__all__ = [
    "Attribution",
    "AttributionIndex",
    "Provenance",
    "Unexplained",
    "attribute",
    "drawn_on",
]

Origin = Literal["upstream-core", "upstream-extra", "config-add", "recipe-kept"]

#: The `recipe-kept` detail a line carries when swage kept it without being able
#: to explain it: a placeholder, still reported to G1, and not conda-forge
#: structure (v1 §3.3.6).
KEPT_UNEXPLAINED = "kept, unexplained"

_T = TypeVar("_T")


@dataclass(frozen=True)
class Provenance:
    """Why a requirement is in the plan. A line without one holds the feedstock."""

    origin: Origin
    #: Where it came from, e.g. ``"extra:pandas"`` or a config file path; never
    #: prose.
    detail: str
    #: How the upstream name became a conda name. None for `recipe-kept`,
    #: which never reaches the resolver at all.
    mapping: Resolution | None = None


@dataclass(frozen=True)
class Unexplained:
    """A line swage cannot account for. G1 fails and the advice fits the case."""

    #: ``unlisted-extra`` and ``nowhere`` are the two G1 failures (v1 §3.3.10);
    #: ``unrecognized-template`` is v1 §3.3.6's; ``renamed`` is v1 §3.2.2's.
    kind: Literal["unlisted-extra", "nowhere", "unrecognized-template", "renamed"]
    #: The requirement as the recipe has it, quoted back in the report.
    text: str
    #: What is wrong, in terms of the recipe and of upstream: the half swage
    #: publishes (DESIGN.md §3.1).
    reason: str
    #: What to do about it, naming swage's config keys; per finding, because the
    #: remedies differ by kind.
    remedy: str
    #: For ``unlisted-extra``, the extras that would explain the line.
    extras: tuple[str, ...] = ()

    @property
    def message(self) -> str:
        """Both halves, which is what swage's own output prints. Joined with `--`,
        since `;` separates findings.
        """
        return f"{self.reason} -- {self.remedy}"


Attribution = Provenance | Unexplained


@dataclass(frozen=True)
class AttributionIndex:
    """Upstream's dependencies arranged by the conda name they resolve to,
    built once per output.
    """

    core: Mapping[str, Resolution | None] = field(default_factory=dict)
    #: conda name -> (extra, resolution) for extras this output draws from.
    listed: Mapping[str, tuple[str, Resolution | None]] = field(default_factory=dict)
    #: conda name -> the unlisted extras it appears under, in declaration order.
    unlisted: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: conda name -> (detail, source) for lines an `embedded_extras` entry
    #: expands a dependency-carried extra into (design-v1.md 4).
    embedded: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    #: conda name -> its position in upstream's declaration order (v1 §6).
    order: Mapping[str, int] = field(default_factory=dict)
    #: upstream's name -> (the requirement, the conda name it resolved to),
    #: where those differ. Advice only, never consulted when attributing (v1
    #: §3.2.2).
    renamed: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    #: conda name -> what upstream declares it as, in the other role than the
    #: section being attributed. Advice only: it changes the sentence, never the
    #: verdict (v1 §3.3.6).
    declared_elsewhere: Mapping[str, str] = field(default_factory=dict)
    #: The recipe section these lines were attributed against, which decides
    #: which half of upstream's metadata is the core one.
    section: str = "run"
    #: The package whose section this is. Every message names it beside the
    #: section, because a recipe states one dependency in `host` and `run`
    #: alike.
    output: str = ""

    @property
    def where(self) -> str:
        """This section, said the way the recipe's maintainer would say it."""
        return section_phrase(self.section, self.output)

    def contains(self, name: str) -> bool:
        """Whether this upstream version asks for ``name`` in any way at all,
        listed and unlisted extras included: the question a removal asks.
        """
        return any(
            key in index
            for key in _keys(name)
            for index in (self.core, self.listed, self.unlisted, self.embedded)
        )


def drawn_on(
    requirement: UpstreamRequirement,
    conda_name: str,
    extra: str,
    from_extras: Mapping[str, frozenset[str]],
) -> bool:
    """Whether this output takes ``requirement`` from ``extra``.

    Matched on upstream's name and on the conda name it resolves to, because
    config is written in upstream's spelling.
    """
    taken = from_extras.get(extra)
    if taken is None:
        return True
    return bool({normalize_name(requirement.name), normalize_name(conda_name)} & taken)


def build_index(
    upstream: UpstreamMetadata,
    listed_extras: Sequence[str],
    resolver: NameResolver,
    core: bool = True,
    section: str = "run",
    output: str = "",
    embedded_extras: Layered[tuple[str, ...]] | None = None,
    from_extras: Mapping[str, frozenset[str]] | None = None,
) -> AttributionIndex:
    """Arrange upstream's dependencies by the conda name they resolve to.

    ``core`` is False for an output built only from extras. ``section``
    decides which upstream list is the core one: `host` is built from
    `[build-system] requires` (v1 §3.3.6), and where that is None the core
    index is empty (v1 §3.6.2). ``output`` is the package whose section this
    is.
    """
    listed_set = set(listed_extras)
    # An extra split across outputs is listed for the packages this one takes
    # and unlisted for the rest (v1 §4).
    taken = from_extras or {}
    host = section == "host"
    upstream_core: Sequence[UpstreamRequirement] = (
        (upstream.build_requires or ()) if host else upstream.dependencies
    )
    other: Sequence[UpstreamRequirement] = (
        upstream.dependencies if host else (upstream.build_requires or ())
    )
    role = _upstream_role("run" if host else "host")

    order: dict[str, int] = {}
    renamed: dict[str, tuple[str, str]] = {}
    elsewhere: dict[str, str] = {}
    for requirement in other:
        name, _ = _entry(requirement, resolver, embedded_extras, upstream.conda_names)
        for key in _keys(name):
            elsewhere.setdefault(key, role)

    core_index: dict[str, Resolution | None] = {}
    if core:
        for requirement in upstream_core:
            name, resolution = _entry(
                requirement, resolver, embedded_extras, upstream.conda_names
            )
            _record_rename(requirement, resolution, renamed)
            for key in _keys(name):
                core_index.setdefault(key, resolution)
                order.setdefault(key, len(order))

    listed_index: dict[str, tuple[str, Resolution | None]] = {}
    unlisted_index: dict[str, list[str]] = {}
    expandable: list[UpstreamRequirement] = list(upstream_core) if core else []
    for extra, requirements in upstream.optional_dependencies.items():
        for requirement in requirements:
            name, resolution = _entry(
                requirement, resolver, embedded_extras, upstream.conda_names
            )
            _record_rename(requirement, resolution, renamed)
            drawn = extra in listed_set and drawn_on(requirement, name, extra, taken)
            for key in _keys(name):
                if drawn:
                    listed_index.setdefault(key, (extra, resolution))
                    order.setdefault(key, len(order))
                elif extra not in unlisted_index.setdefault(key, []):
                    unlisted_index[key].append(extra)
            if drawn:
                expandable.append(requirement)

    # Expansions are recorded last, once every parent has a position, because
    # each inherits its parent's (v1 §6).
    embedded_index: dict[str, tuple[str, str]] = {}
    for requirement in expandable:
        parent, _ = _entry(requirement, resolver, embedded_extras, upstream.conda_names)
        _record_embedded(
            requirement, embedded_extras, embedded_index, order, order.get(parent)
        )

    return AttributionIndex(
        core=core_index,
        listed=listed_index,
        unlisted={name: tuple(extras) for name, extras in unlisted_index.items()},
        embedded=embedded_index,
        order=order,
        renamed=renamed,
        declared_elsewhere=elsewhere,
        section=section,
        output=output,
    )


def _upstream_role(section: str) -> str:
    """What upstream calls a requirement of the list ``section`` is built from
    (v1 §3.3.6).
    """
    return "a build requirement" if section == "host" else "a run dependency"


def _upstream_list(section: str) -> str:
    """That same list, named as the thing a maintainer would go and look at."""
    return (
        "upstream's build-system requirements"
        if section == "host"
        else "upstream's dependencies"
    )


def _record_rename(
    requirement: UpstreamRequirement,
    resolution: Resolution | None,
    into: dict[str, tuple[str, str]],
) -> None:
    """Note that upstream's name for this requirement is not conda-forge's.

    Recorded for the advice alone (v1 §3.2.2): the line is kept and the
    maintainer decides. The requirement's own key is kept beside the conda
    name, since an extra can resolve to a package of its own.
    """
    if resolution is None or resolution.conda_name == requirement.name:
        return
    for key in _keys(requirement.name):
        into.setdefault(key, (requirement.key, resolution.conda_name))


def _record_embedded(
    requirement: UpstreamRequirement,
    embedded_extras: Layered[tuple[str, ...]] | None,
    into: dict[str, tuple[str, str]],
    order: dict[str, int],
    position: int | None,
) -> None:
    """Index the lines config says a dependency-carried extra expands to.

    `config-add` is their origin; the detail names the requirement whose
    extra they stand in for. Each inherits its parent's position (v1 §6).
    """
    if embedded_extras is None or not requirement.extras:
        return
    found = embedded_extras.lookup(requirement.key)
    if found is None:
        return
    lines, source = found
    for line in lines:
        for key in _keys(_bare_name(line)):
            into.setdefault(key, (f"embedded_extras:{requirement.key}", source))
            if position is not None:
                order.setdefault(key, position)


def _keys(name: str) -> tuple[str, ...]:
    """The spellings a conda name may be written under, most exact first.

    conda-forge names are not PEP 503-normalized, so the exact name is tried
    first and the normalized form second.
    """
    normalized = normalize_name(name)
    return (name,) if name == normalized else (name, normalized)


def _find(index: Mapping[str, _T], name: str) -> _T | None:
    for key in _keys(name):
        if key in index:
            return index[key]
    return None


def _entry(
    requirement: UpstreamRequirement,
    resolver: NameResolver,
    embedded_extras: Layered[tuple[str, ...]] | None = None,
    mapped: bool = False,
) -> tuple[str, Resolution | None]:
    """The conda name this requirement would appear under, and how it got there.

    ``mapped`` says the reader has already answered this
    (`UpstreamMetadata.conda_names`). Resolution is keyed on the requirement,
    extra included (v1 §3.2). An unresolvable name is still indexed, under
    its normalized spelling; whether swage may act on it is G2's question.
    """
    resolution = resolve_requirement(requirement, resolver, embedded_extras, mapped)
    if resolution is not None:
        return resolution.conda_name, resolution
    return normalize_name(requirement.name), None


def attribute(
    line: ParsedLine,
    index: AttributionIndex,
    recipe_owned: RecipeOwned,
    added: Sequence[AddedRequirement] = (),
) -> Attribution:
    """Explain one requirement line, or say precisely why it cannot be."""
    # 1. recipe-owned. First, so a structural line never reaches the resolver
    # (v1 §3.3.6).
    if line.recipe_owned(recipe_owned):
        return Provenance(origin="recipe-kept", detail=_owned_detail(line))

    if expansions := line.platform_expansions:
        # The `noarch_platform` idiom: explained when every name it can take is
        # explained. Attributed, never rewritten (DESIGN.md §9.4).
        explanations = [
            attribute(parse_line(name), index, recipe_owned, added)
            for name in expansions
        ]
        if all(isinstance(item, Provenance) for item in explanations):
            # The upstream half is what the line exists to deliver; the other
            # half is usually `python`.
            return next(
                (
                    item
                    for item in explanations
                    if isinstance(item, Provenance) and item.origin != "recipe-kept"
                ),
                explanations[0],
            )

    if line.templated_name:
        # Preserved unchanged and unexplained, so G1 still holds the feedstock.
        # `_bless` says which list, if any, could describe it.
        return Unexplained(
            kind="unrecognized-template",
            text=line.text,
            reason=(
                f"{fenced(line.text)} in {index.where} is a template swage "
                "does not recognize, and is preserved unchanged"
            ),
            remedy=_bless(line),
        )

    name = line.name
    keys = set(_keys(name))

    # 2. upstream core. Membership is the test: a name that resolved to nothing
    # is still explained, and G2 asks whether swage may act on it.
    if any(key in index.core for key in keys):
        return Provenance(
            origin="upstream-core", detail="upstream", mapping=_find(index.core, name)
        )

    # 3. a listed extra, before 4: a dependency in both a listed and an
    #    unlisted extra is explained by the listed one.
    if (from_extra := _find(index.listed, name)) is not None:
        extra, resolution = from_extra
        return Provenance(
            origin="upstream-extra", detail=f"extra:{extra}", mapping=resolution
        )

    # 5a. an `embedded_extras` expansion: upstream asked for the extra, config
    #     said what it means on conda-forge, so the line is accounted for.
    if (expansion := _find(index.embedded, name)) is not None:
        detail, source = expansion
        return Provenance(origin="config-add", detail=f"{detail} ({source})")

    # 5b. add_requirements, before 6 so a declared conda-forge-only dependency
    #     is explained rather than reported as coming from nowhere.
    for entry in added:
        if keys & set(_keys(_bare_name(entry.text))):
            return Provenance(origin="config-add", detail=entry.source)

    # 4. an unlisted extra. After 5, because a maintainer who wrote the line
    #    into add_requirements has already answered the question.
    if (unlisted := _find(index.unlisted, name)) is not None:
        named = ", ".join(fenced(extra) for extra in unlisted)
        return Unexplained(
            kind="unlisted-extra",
            text=line.text,
            extras=tuple(unlisted),
            reason=(
                f"{fenced(line.text)} in {index.where} comes from upstream "
                f"extra {named}, which this output does not list"
            ),
            remedy="add the extra so swage maintains the line, or remove the line",
        )

    # 6a. upstream's own spelling of a package conda-forge renames: same verdict
    # as 6, different remedy (v1 §3.2.2).
    if (rename := _find(index.renamed, name)) is not None:
        declared_as, conda_name = rename
        also = (
            "" if declared_as == line.name else f", declared as {fenced(declared_as)}"
        )
        return Unexplained(
            kind="renamed",
            text=line.text,
            reason=(
                f"{fenced(line.text)} in {index.where} is upstream's "
                f"name{also} for what conda-forge publishes as "
                f"{fenced(conda_name)}, which swage renders instead"
            ),
            remedy=(
                f"drop this line, or map {fenced(declared_as)} to "
                f"{fenced(line.name)} in name_map if this feedstock means "
                "conda-forge's package of that name"
            ),
        )

    # 5b. declared by upstream, in the other role. Still unexplained, but the
    # sentence says so (v1 §3.3.6).
    if (role := _find(index.declared_elsewhere, name)) is not None:
        return Unexplained(
            kind="nowhere",
            text=line.text,
            reason=(
                f"{fenced(line.text)} in {index.where} is declared by upstream "
                f"as {role}, and that section is reconciled against "
                f"{_upstream_list(index.section)}"
            ),
            remedy=(
                "drop it, or declare it in add_requirements if conda-forge "
                "needs it there"
            ),
        )

    # 6. nowhere at all.
    return Unexplained(
        kind="nowhere",
        text=line.text,
        reason=(f"{fenced(line.text)} is in {index.where} and in no upstream version"),
        remedy=(
            "drop it, declare it in add_requirements if conda-forge needs it "
            "for good, or in temporary_requirements if it is working around "
            "another package's metadata and should be re-checked at every "
            "version bump"
        ),
    )


def _bless(line: ParsedLine) -> str:
    """What to do about a template swage does not recognize.

    A function call can be blessed in `functions`; a whole-name variable in
    `variables`; anything else config cannot describe, and the remedy says so.
    """
    if line.function is not None:
        return (
            f"add {fenced(line.function)} to recipe_owned.functions in config "
            "to bless it"
        )
    if variable := line.interpolated_variable:
        return (
            f"add {fenced(variable)} to recipe_owned.variables in config, if "
            "this is a build variant's key rather than a package name"
        )
    return (
        "config cannot account for a name a recipe interpolates rather than "
        "calls. Where it names another output of this recipe, "
        "`${{ pin_subpackage(...) }}` is the form swage already understands"
    )


def _owned_detail(line: ParsedLine) -> str:
    if line.function is not None:
        return f"recipe_owned.functions:{line.function}"
    if variable := line.interpolated_variable:
        return f"recipe_owned.variables:{variable}"
    return f"recipe_owned.names:{line.name}"


def _bare_name(text: str) -> str:
    """The name out of a config line like ``grpcio-gcp >=0.2.2``."""
    return text.split(None, 1)[0]
