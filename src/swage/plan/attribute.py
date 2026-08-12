"""Explain every requirement already in a recipe, or refuse to (DESIGN.md 3.3.10).

A dependency swage cannot justify is a dependency it will silently stop
maintaining, so every line has to be traceable to something: upstream metadata,
an explicit config entry, or a recognized recipe-owned line. That is gate G1,
and `Provenance` is the mechanism by which it is *checked* rather than assumed.

Attribution runs in a fixed order, and the outcomes are not interchangeable --
each carries different advice because each has a different fix:

1. **recipe-owned** -- a recognized template or `python`/`pip` (3.3.6)
2. **upstream core** -- in the metadata's own dependency list
3. **a listed extra** -- in an extra this output draws from
4. **an unlisted extra** -- upstream, but only under an extra this feedstock
   does not list. **G1 fails, naming the extra**
5. **`add_requirements`** -- a conda-forge-only dependency someone wrote down
6. **nowhere at all** -- in no upstream version. **G1 fails**, pointing at
   `add_requirements`

**Order matters between 3 and 4**: a dependency belonging to both a listed and
an unlisted extra is explained by the listed one and needs no further thought.

**The two failures are one gate with different advice, and the difference is
the point.** Case 6 sends the maintainer to `add_requirements`. Case 4 must
not -- there the fix is almost always to *list the extra*, so that swage
maintains the line from now on, and pointing at `add_requirements` would
quietly convert a maintainable dependency into a hand-managed one. Same
verdict, opposite remedies.

Case 4 is also what makes ignoring unlisted extras safe. Without it a recipe
could carry an unlisted extra's dependencies with nothing maintaining them,
going stale as upstream moves and never saying so.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, TypeVar

from swage.config import AddedRequirement, Layered, RecipeOwned
from swage.mapping import NameResolver, Resolution, normalize_name
from swage.upstream import UpstreamMetadata, UpstreamRequirement

from .lines import ParsedLine

__all__ = ["Attribution", "AttributionIndex", "Provenance", "Unexplained", "attribute"]

Origin = Literal["upstream-core", "upstream-extra", "config-add", "recipe-kept"]

_T = TypeVar("_T")


@dataclass(frozen=True)
class Provenance:
    """Why a requirement is in the plan.

    Not decoration: a line without one is a line swage cannot justify, and a
    plan containing one is not eligible for automerge.
    """

    origin: Origin
    #: Where it came from, e.g. ``"extra:pandas"`` or
    #: ``"config/families/google-cloud.yaml"``. A file path or a named layer,
    #: never prose, so `swage explain` always points somewhere specific.
    detail: str
    #: How the upstream name became a conda name. None for `recipe-kept`,
    #: which never reaches the resolver at all.
    mapping: Resolution | None = None


@dataclass(frozen=True)
class Unexplained:
    """A line swage cannot account for. G1 fails and the advice fits the case."""

    #: ``unlisted-extra`` and ``nowhere`` are the two G1 failures of 3.3.10;
    #: ``unrecognized-template`` is 3.3.6's, where the line is preserved but
    #: still unexplained.
    kind: Literal["unlisted-extra", "nowhere", "unrecognized-template"]
    #: The requirement as the recipe has it, quoted back in the report.
    text: str
    #: The whole message, including the remedy. The remedies differ between
    #: kinds and confusing them gives confidently wrong advice.
    reason: str
    #: For ``unlisted-extra``, the extras that would explain the line.
    extras: tuple[str, ...] = ()


Attribution = Provenance | Unexplained


@dataclass(frozen=True)
class AttributionIndex:
    """Upstream's dependencies arranged by the conda name they resolve to.

    Built once per output rather than per line. Detecting case 4 means mapping
    *every* unlisted extra's dependencies through the resolver to compare them
    against conda-side names -- real work rather than a lookup, and it earns
    its cost by being the difference between the right advice and confidently
    wrong advice on a case that recurs.
    """

    core: Mapping[str, Resolution | None] = field(default_factory=dict)
    #: conda name -> (extra, resolution) for extras this output draws from.
    listed: Mapping[str, tuple[str, Resolution | None]] = field(default_factory=dict)
    #: conda name -> the unlisted extras it appears under, in declaration order.
    unlisted: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: conda name -> (detail, source) for lines an `embedded_extras` entry
    #: expands a dependency-carried extra into (DESIGN.md 4).
    embedded: Mapping[str, tuple[str, str]] = field(default_factory=dict)


def build_index(
    upstream: UpstreamMetadata,
    listed_extras: Sequence[str],
    resolver: NameResolver,
    core: bool = True,
    section: str = "run",
    embedded_extras: Layered[tuple[str, ...]] | None = None,
) -> AttributionIndex:
    """Arrange upstream's dependencies by the conda name they resolve to.

    ``core`` is False for an output built only from extras -- a metapackage
    whose `run` folds in extras without upstream's own dependencies -- where a
    core dependency appearing in the recipe is not explained by this output.

    ``section`` decides *which* upstream list is the core one. `host` is
    reconciled against ``[build-system] requires``, not against the runtime
    dependencies: `flit-core ==3.12.0` looks like a conda-forge convention and
    is upstream's own pin (DESIGN.md 3.3.6). Indexing the wrong list reports
    every build backend as coming from nowhere, which is how 8 of the corpus's
    recipes failed G1 before this distinction existed.

    Where ``build_requires`` is None -- core metadata carries no build-system
    table at all (DESIGN.md 3.6.2) -- the core index is empty, and `host`
    cannot be attributed from this source. That is a signal for the caller to
    leave `host` alone rather than report every line in it.
    """
    listed_set = set(listed_extras)
    upstream_core: Sequence[UpstreamRequirement] = (
        (upstream.build_requires or ()) if section == "host" else upstream.dependencies
    )

    core_index: dict[str, Resolution | None] = {}
    if core:
        for requirement in upstream_core:
            name, resolution = _entry(requirement, resolver)
            for key in _keys(name):
                core_index.setdefault(key, resolution)

    listed_index: dict[str, tuple[str, Resolution | None]] = {}
    unlisted_index: dict[str, list[str]] = {}
    embedded_index: dict[str, tuple[str, str]] = {}
    for extra, requirements in upstream.optional_dependencies.items():
        for requirement in requirements:
            if extra in listed_set:
                _record_embedded(requirement, embedded_extras, embedded_index)
            name, resolution = _entry(requirement, resolver)
            for key in _keys(name):
                if extra in listed_set:
                    listed_index.setdefault(key, (extra, resolution))
                elif extra not in unlisted_index.setdefault(key, []):
                    unlisted_index[key].append(extra)

    for requirement in upstream_core:
        _record_embedded(requirement, embedded_extras, embedded_index)

    return AttributionIndex(
        core=core_index,
        listed=listed_index,
        unlisted={name: tuple(extras) for name, extras in unlisted_index.items()},
        embedded=embedded_index,
    )


def _record_embedded(
    requirement: UpstreamRequirement,
    embedded_extras: Layered[tuple[str, ...]] | None,
    into: dict[str, tuple[str, str]],
) -> None:
    """Index the lines config says a dependency-carried extra expands to.

    `pyhive[hive-pure-sasl]` has no conda-forge equivalent, so the maintainer
    writes out what it pulls in (DESIGN.md 4) and swage splices that block in
    behind `# start` / `# end` markers. Those lines are in the recipe because
    config put them there, so `config-add` is their origin -- but the detail
    names the requirement whose extra they stand in for, which is the part that
    makes the entry findable.
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


def _keys(name: str) -> tuple[str, ...]:
    """The spellings a conda name may be written under, most exact first.

    **conda-forge package names are not PEP 503-normalized.**
    `config/name-map.yaml` maps `msal-extensions` to `msal_extensions`, and
    `facebook-business` and `slack-sdk` do the same -- the underscore is the
    real package name, not a spelling mistake. Normalizing before comparison
    turns those into `msal-extensions` and loses the match, which reports a
    plainly upstream dependency as coming from nowhere.

    So the exact conda name is tried first and the normalized form second,
    which still catches a recipe that writes `Requests` or `msal_extensions`
    where the index holds the other spelling.
    """
    normalized = normalize_name(name)
    return (name,) if name == normalized else (name, normalized)


def _find(index: Mapping[str, _T], name: str) -> _T | None:
    for key in _keys(name):
        if key in index:
            return index[key]
    return None


def _entry(
    requirement: UpstreamRequirement, resolver: NameResolver
) -> tuple[str, Resolution | None]:
    """The conda name this requirement would appear under, and how it got there.

    Resolution is keyed on the requirement rather than the bare name, because a
    dependency carrying an extra is frequently a different conda package --
    `google-api-core[grpc]` is `google-api-core-grpc` (DESIGN.md 3.2).

    An unresolvable name still gets indexed, under its normalized spelling, so
    a recipe line matching upstream's own name is attributed rather than
    reported as coming from nowhere. Whether swage may act on it is G2's
    question, not this one, and answering it here would give the maintainer the
    wrong problem to solve.
    """
    resolution = resolver.resolve(requirement.key)
    if resolution is None and requirement.extras:
        resolution = resolver.resolve(requirement.name)
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
    # 1. recipe-owned. Checked first so a structural line never reaches the
    #    resolver, which is what keeps G2 from blocking every multi-output
    #    feedstock in the fleet (DESIGN.md 3.3.6).
    if line.recipe_owned(recipe_owned):
        return Provenance(origin="recipe-kept", detail=_owned_detail(line))

    if line.templated_name:
        # Preserved unchanged -- swage never rewrites what it does not
        # understand -- but unexplained, so G1 still stops the feedstock.
        return Unexplained(
            kind="unrecognized-template",
            text=line.text,
            reason=(
                f"unrecognized template {line.name!r}; preserved unchanged, "
                "add it to recipe_owned in config to bless it"
            ),
        )

    name = line.name
    keys = set(_keys(name))

    # 2. upstream core. Membership is the test, not the value: a name that
    #    resolved to nothing is indexed with a None mapping, and that is still
    #    an explanation -- whether swage may act on it is G2's question.
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
        named = ", ".join(repr(extra) for extra in unlisted)
        return Unexplained(
            kind="unlisted-extra",
            text=line.text,
            extras=tuple(unlisted),
            reason=(
                f"{line.name!r} comes from upstream extra {named}, which this "
                "output does not list; add the extra so swage maintains the "
                "line, or remove the line"
            ),
        )

    # 6. nowhere at all.
    return Unexplained(
        kind="nowhere",
        text=line.text,
        reason=(
            f"{line.name!r} is in the recipe and in no upstream version; "
            "declare it in add_requirements to keep it, or drop it"
        ),
    )


def _owned_detail(line: ParsedLine) -> str:
    return (
        f"recipe_owned.functions:{line.function}"
        if line.function is not None
        else f"recipe_owned.names:{line.name}"
    )


def _bare_name(text: str) -> str:
    """The name out of a config line like ``grpcio-gcp >=0.2.2``."""
    return text.split(None, 1)[0]
