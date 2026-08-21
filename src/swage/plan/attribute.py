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
the point.** Case 6 offers `add_requirements`. Case 4 must not -- there the fix
is almost always to *list the extra*, so that swage maintains the line from now
on, and pointing at `add_requirements` would quietly convert a maintainable
dependency into a hand-managed one. Same verdict, opposite remedies.

Case 6 offers it as one of *three* answers rather than as the resolution, and
the third is the one a tool would get wrong. A temporary constraint working
around another conda-forge package's broken metadata must **not** be blessed:
an entry silences this gate for good, and the constraint then outlives the bug
it exists for with nothing left to notice. Leaving it unexplained is what makes
swage ask again at the next version bump, which is when somebody should check
whether the upstream fix has landed (DESIGN.md 3.3.7).

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

#: The `recipe-kept` detail a line carries when swage kept it without being
#: able to explain it. `recipe-kept` is an allowlist and never a fallback
#: (DESIGN.md 3.3.6), so this is a placeholder rather than a claim: the line is
#: still reported to G1, and it is *not* conda-forge structure, which is what
#: the report and the ordering rule both have to know.
KEPT_UNEXPLAINED = "kept, unexplained"

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
    #: still unexplained; ``renamed`` is 3.2.2's, where upstream declares this
    #: very name and conda-forge publishes what it means under another.
    kind: Literal["unlisted-extra", "nowhere", "unrecognized-template", "renamed"]
    #: The requirement as the recipe has it, quoted back in the report.
    text: str
    #: What is wrong, said in terms of the recipe and of upstream and of
    #: nothing else. **This is the half swage publishes**: it is what a
    #: comment on the feedstock's own pull request says, so it names the line,
    #: the section it sits in and what upstream does or does not declare, and
    #: never a key in swage's config (DESIGN.md 5.4, CLAUDE.md).
    reason: str
    #: What to do about it, which is where swage's own config keys belong. The
    #: remedies differ between kinds and confusing them gives confidently wrong
    #: advice, so it is carried per finding rather than per check.
    remedy: str
    #: For ``unlisted-extra``, the extras that would explain the line.
    extras: tuple[str, ...] = ()

    @property
    def message(self) -> str:
        """Both halves, which is what swage's own output prints.

        Joined with `--` rather than `;`, because `;` is what separates one of
        a check's findings from the next and a remedy containing the separator
        reads as a second finding.
        """
        return f"{self.reason} -- {self.remedy}"


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
    #: conda name -> its position in upstream's own declaration order, which
    #: DESIGN.md 6 orders the rendered section by. Recorded here because it
    #: comes from the same traversal that built the rest of the index, and
    #: recomputing it elsewhere is a second thing that can drift.
    order: Mapping[str, int] = field(default_factory=dict)
    #: *upstream's* name -> (the requirement it came from, the conda name it
    #: resolved to), for the requirements where those differ. Advice only, and
    #: deliberately not consulted when attributing a line: a recipe line under
    #: upstream's spelling names a package conda-forge may genuinely publish,
    #: so it is kept and reported rather than treated as the resolved one and
    #: deleted (DESIGN.md 3.2.2).
    renamed: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    #: conda name -> what upstream declares it as, for names upstream declares
    #: in the *other* role than the section being attributed: a run dependency
    #: while `host` is reconciled against `[build-system] requires`, or a build
    #: requirement while `run` is reconciled against the dependencies.
    #:
    #: Advice only, exactly like `renamed`, and for the same reason: a
    #: dependency upstream needs to *build* is not thereby one it needs to
    #: *run*, so this never explains the line. What it changes is the sentence
    #: the maintainer reads, which would otherwise say upstream does not
    #: declare a name upstream declares (DESIGN.md 3.3.6).
    declared_elsewhere: Mapping[str, str] = field(default_factory=dict)
    #: The recipe section these lines were attributed against, which decides
    #: which half of upstream's metadata is the core one.
    section: str = "run"
    #: The package whose section this is -- the output's name, or the
    #: recipe's where it builds one package. Every message about a line names
    #: it beside the section, because a name alone does not identify a
    #: finding: a
    #: recipe states the same dependency in `host` and in `run` routinely, and
    #: the two are reconciled against different halves of upstream's metadata.
    #: `esmf` states `hdf5` three times across two sections and
    #: `netcdf-fortran` three more, which arrived as six findings no two of
    #: which could be told apart.
    output: str = ""

    @property
    def where(self) -> str:
        """This section, said the way the recipe's maintainer would say it."""
        return section_phrase(self.section, self.output)

    def contains(self, name: str) -> bool:
        """Whether this upstream version asks for ``name`` in any way at all.

        Deliberately spans listed *and* unlisted extras. The question a removal
        asks is "did upstream declare this?", not "does this output draw on
        it" -- a dependency that moved into an extra the feedstock does not
        list has not been dropped by upstream, and treating it as dropped
        would delete a line on evidence that does not exist.
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

    True for every requirement of an extra folded in whole, and only for the
    named ones where the extra is split across outputs. Matched on upstream's
    name and on the conda name it resolves to, because config is written in
    upstream's spelling and read beside a recipe written in conda-forge's.
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

    ``output`` is the package whose section this is, and every message about a
    line in it names that package beside the section. Empty only where the
    caller has no name to give -- a recipe that builds one package still names
    it, at the top level rather than in an `outputs` list.
    """
    listed_set = set(listed_extras)
    # An extra split across outputs is listed here for the packages this one
    # takes and unlisted for the rest, which is what it is: a line for
    # somebody else's half of the split comes from an extra this output does
    # not draw on (DESIGN.md 4).
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
        name, _ = _entry(requirement, resolver, embedded_extras)
        for key in _keys(name):
            elsewhere.setdefault(key, role)

    core_index: dict[str, Resolution | None] = {}
    if core:
        for requirement in upstream_core:
            name, resolution = _entry(requirement, resolver, embedded_extras)
            _record_rename(requirement, resolution, renamed)
            for key in _keys(name):
                core_index.setdefault(key, resolution)
                order.setdefault(key, len(order))

    listed_index: dict[str, tuple[str, Resolution | None]] = {}
    unlisted_index: dict[str, list[str]] = {}
    expandable: list[UpstreamRequirement] = list(upstream_core) if core else []
    for extra, requirements in upstream.optional_dependencies.items():
        for requirement in requirements:
            name, resolution = _entry(requirement, resolver, embedded_extras)
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
    # each inherits its parent's. An `embedded_extras` block is an *island*
    # sitting immediately after the requirement whose extra it stands in for
    # (DESIGN.md 6), not a trailing addition -- giving it its own position
    # would scatter `pure-sasl`, `thrift` and `thrift_sasl` away from the
    # `pyhive` line that explains them.
    embedded_index: dict[str, tuple[str, str]] = {}
    for requirement in expandable:
        parent, _ = _entry(requirement, resolver, embedded_extras)
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
    """What upstream calls a requirement of the list ``section`` is built from.

    `host` is built from `[build-system] requires` and every other section
    from the dependencies, which is the same split `build_index` reads the two
    lists by (DESIGN.md 3.3.6).
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

    Recorded for the *advice* alone (DESIGN.md 3.2.2). A recipe carrying the
    upstream spelling is carrying a name conda-forge may genuinely publish --
    `psycopg2-binary` is a real package there -- so the line is kept and the
    maintainer decides. What this changes is what the report says about it:
    without this, a dependency upstream declares outright is reported as coming
    from no upstream version, and the maintainer is sent to `add_requirements`
    to hand-manage a line swage would happily maintain.

    The requirement's own key is kept beside the conda name because they are
    not always the same question. `google-api-core[grpc]` resolves to
    `google-api-core-grpc`, and a bare `google-api-core` line in that recipe is
    upstream's *base* name rather than a misspelling of anything.
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

    `pyhive[hive-pure-sasl]` has no conda-forge equivalent, so the maintainer
    writes out what it pulls in (DESIGN.md 4) and swage splices that block in
    behind `# start` / `# end` markers. Those lines are in the recipe because
    config put them there, so `config-add` is their origin -- but the detail
    names the requirement whose extra they stand in for, which is the part that
    makes the entry findable.

    Each inherits its parent's position. The block is an island sitting where
    the parent sits, so `pure-sasl`, `thrift` and `thrift_sasl` stay under the
    `pyhive` line rather than drifting into the alphabetized trailing block
    that `add_requirements` lines belong in. Sharing one position is
    deliberate: sorting is stable, so the expansion keeps the order config
    wrote it in.
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
    requirement: UpstreamRequirement,
    resolver: NameResolver,
    embedded_extras: Layered[tuple[str, ...]] | None = None,
) -> tuple[str, Resolution | None]:
    """The conda name this requirement would appear under, and how it got there.

    Resolution is keyed on the requirement rather than the bare name, because
    conda-forge frequently publishes an extra under a name of its own --
    `google-api-core[grpc]` is `google-api-core-grpc`, an output carrying the
    base package plus what the extra pulls in (DESIGN.md 3.2).

    An unresolvable name still gets indexed, under its normalized spelling, so
    a recipe line matching upstream's own name is attributed rather than
    reported as coming from nowhere. Whether swage may act on it is G2's
    question, not this one, and answering it here would give the maintainer the
    wrong problem to solve.

    The same holds for a requirement whose extra nothing accounts for: it is
    indexed under the bare name, because the recipe line really is explained by
    upstream, and the resolution carries the dropped extra for G2 to stop on.
    """
    resolution = resolve_requirement(requirement, resolver, embedded_extras)
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

    if expansions := line.platform_expansions:
        # The `noarch_platform` idiom: one line naming a different package per
        # platform. Explained when *every* name it can take is explained --
        # the same all-or-nothing rule `recipe_owned` applies to the
        # interpolated virtual package, and for the same reason. A line half
        # of whose expansions come from nowhere is one swage cannot account
        # for.
        #
        # **Attributed, never rewritten.** The maintainer's spelling stays as
        # written. What this buys is that swage stops reporting the line as
        # unreadable, and stops proposing its own copy of the same dependency
        # beside it.
        explanations = [
            attribute(parse_line(name), index, recipe_owned, added)
            for name in expansions
        ]
        if all(isinstance(item, Provenance) for item in explanations):
            # The upstream half is what the line exists to deliver; the other
            # half is usually `python`, filler standing in for the empty entry
            # a bare `if` would leave.
            return next(
                (
                    item
                    for item in explanations
                    if isinstance(item, Provenance) and item.origin != "recipe-kept"
                ),
                explanations[0],
            )

    if line.templated_name:
        # Preserved unchanged -- swage never rewrites what it does not
        # understand -- but unexplained, so G1 still stops the feedstock.
        #
        # **The two cases have different answers, and offering the wrong one
        # sends a maintainer to edit a file that cannot help them.**
        # `recipe_owned` blesses a *call*, by naming the function, and `names`
        # holds literals. A name a recipe interpolates without calling
        # anything -- `parsl`'s `${{ name }}-with-monitoring` -- is described by
        # neither, so `recipe_owned()` refuses it before it ever consults
        # `names`. Telling that maintainer to add it there was advice nobody
        # could follow.
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

    # 6a. upstream's own spelling of a package conda-forge renames. Same
    #     verdict as 6 and a different remedy, which is the whole reason it is
    #     told apart (DESIGN.md 3.2.2): upstream declares this name outright,
    #     so `add_requirements` would convert a line swage maintains into one
    #     nobody does.
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

    # 5b. declared by upstream, in the other role. Still unexplained -- a
    #     dependency upstream needs to build is not one it needs to run, and
    #     vice versa -- but "in no upstream version" would be false, and it is
    #     the sentence somebody decides on (DESIGN.md 3.3.6).
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

    Three shapes and three answers. An expression naming a function can be
    blessed, so the remedy names the function and the list it goes in. A whole
    name that is one variable is a build variant's own key and goes in
    `variables`. Anything else interpolates a variable into a name the recipe
    then builds on, which config cannot describe -- so the remedy says so and
    points at the expression the blessed functions already cover, which is
    what a recipe referring to another of its own outputs should be written as
    anyway.
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
