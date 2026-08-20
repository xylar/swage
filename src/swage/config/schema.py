"""Pydantic schema for the quirks database (DESIGN.md 4).

Every layer of the database is validated against these models with
``extra="forbid"``, so a mistyped key is a startup error rather than a silently
ignored setting.
"""

from __future__ import annotations

from typing import Annotated, Literal

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from pydantic import BaseModel, ConfigDict, Field, model_validator

from swage.naming import normalize_extra

__all__ = [
    "AddRequirements",
    "ArchiveUpstream",
    "Defaults",
    "DynamicPolicy",
    "ExtrasAsOutputs",
    "Family",
    "Feedstock",
    "GitHubUpstream",
    "Output",
    "OutputRun",
    "Override",
    "Quirks",
    "RecipeOwned",
    "RemovalPolicy",
    "RunConstraint",
    "SourceVersionPolicy",
    "TestMatrixPolicy",
    "TrustLevel",
    "Upstream",
]

#: ``never`` writes to the feedstock at all; ``propose`` pushes a change the
#: gates accounted for and leaves the labeling to a person; ``auto`` labels it
#: too (DESIGN.md 5.4).
#:
#: ``never`` rather than ``off``, which YAML 1.1 reads as the boolean ``False``
#: -- along with ``no``, ``yes`` and ``on``. The rung a maintainer types least
#: often is the one that can least afford a spelling that needs quoting.
#:
#: **Whether swage pushes is the gates' answer, not this key's.** A rung is a
#: standing decision about a feedstock -- leave it alone, or let it merge
#: unattended -- and whether one particular change is understood well enough to
#: offer is a fact about that change, which the gates already compute.
TrustLevel = Literal["never", "propose", "auto"]

#: Whether an upstream-dropped removal may merge unattended (DESIGN.md 3.3.8).
#: A proving period, not a permanent rule -- promoted deliberately, in a config
#: commit, once there is a body of reviewed removals swage classified correctly.
RemovalPolicy = Literal["review", "auto"]

#: Whether a dependency list upstream computed at build time may merge
#: unattended (DESIGN.md 3.6.3). `trust` says a PEP 643 `Dynamic: Requires-Dist`
#: is good enough; `review` holds it for a human. The escape hatch if G10 turns
#: out to cost more than it saves.
DynamicPolicy = Literal["review", "trust"]

#: Whether a recipe whose python test matrix swage completed may merge
#: unattended (DESIGN.md 3.7). `review` holds it for a human; `auto` treats it
#: as an ordinary change. A proving period rather than a permanent rule, and
#: the reason it exists is that this is the first thing swage writes outside a
#: requirements block -- so "only requirements changed" stopped being true by
#: construction and became a claim somebody should check while the behavior
#: is new.
TestMatrixPolicy = Literal["review", "auto"]
#: Whether swage may set the version a second source is pinned at, where the
#: rest of the recipe requires one it does not build (DESIGN.md 3.6.4).
SourceVersionPolicy = Literal["never", "auto"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _check_extras(names: tuple[str, ...], field: str) -> None:
    """Refuse an extra spelled any way but the one swage reads it as.

    swage PEP 685-normalizes every extra name it reads from upstream, so an
    entry written the way `pyproject.toml` spells it -- `bigquery_v2`,
    `apache.iceberg` -- would never match. Nothing would report that: a stale
    `embedded_extras` key leaves an extra unexpanded, and a stale `skip` entry
    makes G3 name an extra the maintainer had already declined. Naming the
    right spelling here is cheaper than either silence.
    """
    for name in names:
        normalized = normalize_extra(name)
        if normalized != name:
            raise ValueError(
                f"{field}: extra {name!r} is not normalized; write "
                f"{normalized!r} (PEP 685)"
            )


class GitHubUpstream(_Model):
    """Metadata read from a file in a git tag, e.g. the airflow monorepo."""

    source: Literal["github"]
    repo: str
    tag: str
    metadata: str


class ArchiveUpstream(_Model):
    """Metadata read from the archive the recipe's ``source.url`` pins.

    Named for what it reads rather than for where the archive is hosted: the
    google-cloud family fetches sdists from PyPI, but `openlineage-python`
    pins a GitHub release tarball, and both are the same operation.

    The recipe's `source.url` and `sha256` locate the archive by themselves,
    so there is nothing to name the project with: a `project` key sat here
    unread from the first version of this schema, and `extra="forbid"` means
    removing it turns any config that set it into an error rather than
    leaving it silently doing nothing.
    """

    source: Literal["archive"]
    #: Where inside the archive the metadata is, relative to its single
    #: top-level directory -- `client/python/pyproject.toml` rather than
    #: `OpenLineage-1.40.1/client/python/pyproject.toml`, so the path survives
    #: a version bump. Only needed where the file at the root is not the right
    #: one, which is the monorepo case: the `OpenLineage` tarball carries
    #: seven `pyproject.toml` files and the root one describes no package.
    #:
    #: This is the same answer the airflow family already has in
    #: `GitHubUpstream.metadata`, for the same reason -- which subdirectory
    #: holds the package is not something swage can infer, and guessing
    #: wrongly would reconcile a recipe against a different project entirely.
    metadata: str | None = None


Upstream = Annotated[GitHubUpstream | ArchiveUpstream, Field(discriminator="source")]


class ExtrasAsOutputs(_Model):
    """Upstream extras that become separate conda outputs.

    ``supported`` and ``skip`` together must cover every extra upstream
    declares; an extra in neither list stops the feedstock (gate G3).
    """

    suffix: str
    supported: tuple[str, ...] = ()
    skip: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _disjoint(self) -> ExtrasAsOutputs:
        _check_extras(self.supported, "supported")
        _check_extras(self.skip, "skip")
        both = sorted(set(self.supported) & set(self.skip))
        if both:
            raise ValueError(
                f"extras listed in both 'supported' and 'skip': {', '.join(both)}"
            )
        return self


class Override(_Model):
    """A bound this feedstock states that upstream does not, and why.

    **Recording it is what makes it a decision.** A recipe constraint that
    differs from upstream's is drift by default -- swage reconciles it, in
    either direction, and the change is visible in the plan and in the diff.
    An entry here says the difference is deliberate, and `reason` is the only
    place the next reader can learn what it is for (DESIGN.md 3.3.14).

    ``bound`` is the *additional* constraint rather than the whole specifier:
    it is intersected with what upstream declares.
    """

    bound: str
    reason: str

    @model_validator(mode="after")
    def _says_why(self) -> Override:
        if not self.bound.strip():
            raise ValueError(
                "a constraint that says nothing tightens nothing -- drop the entry"
            )
        try:
            SpecifierSet(self.bound)
        except InvalidSpecifier as exc:
            raise ValueError(
                f"{self.bound!r} is not a version constraint: {exc}"
            ) from exc
        said = self.reason.strip()
        if not said or said.lower() == "todo":
            raise ValueError(
                f"{self.bound!r} needs a reason saying why this feedstock states a "
                "bound upstream does not"
            )
        return self


class OutputRun(_Model):
    """What an existing output's ``run`` section should be built from."""

    core: bool = False
    extras: tuple[str, ...] = ()
    #: Extra name -> the packages of it this output takes, where an extra is
    #: split across several outputs rather than folded whole into one.
    #:
    #: `wetterdienst` publishes upstream's `export` extra as two packages
    #: because one of its dependencies needs a later python than the rest:
    #: `-with-export-without-zarr` carries the other four at the recipe's
    #: floor, and `-with-export` carries `zarr` above it. Listing `export`
    #: whole in either output would write all five into it (DESIGN.md 4).
    #:
    #: An extra named here is drawn on exactly as one in `extras` is, so it is
    #: accounted for at G3 and its lines carry the same provenance. What
    #: differs is that the other outputs' claims are not this output's: a
    #: package no output takes is reported, because a split nobody completes
    #: is how a dependency goes missing.
    from_extras: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    #: Upstream extras this output deliberately does not fold in, and the
    #: thing that opts the feedstock into exhaustiveness (G3, DESIGN.md 4).
    #:
    #: Without it the `outputs[].run` shape has nowhere to record "considered
    #: and declined": `extras_as_outputs.skip` belongs to the other shape, and
    #: borrowing it would mean declaring an output-naming suffix on a feedstock
    #: that publishes no extras as outputs at all. A feedstock with nowhere to
    #: write the decision can never opt in, which is the opposite of what an
    #: opt-in is for.
    skip: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _normalized(self) -> OutputRun:
        _check_extras(self.extras, "extras")
        _check_extras(self.skip, "skip")
        _check_extras(tuple(self.from_extras), "from_extras")
        both = sorted(set(self.extras) & set(self.skip))
        if both:
            raise ValueError(
                f"extras listed in both 'extras' and 'skip': {', '.join(both)}"
            )
        # An extra is taken whole or in part, never both: the two say different
        # things about the same extra and nothing decides which wins.
        split = sorted(set(self.from_extras) & (set(self.extras) | set(self.skip)))
        if split:
            raise ValueError(
                "extras listed in 'from_extras' and also in 'extras' or "
                f"'skip': {', '.join(split)}"
            )
        empty = sorted(name for name, taken in self.from_extras.items() if not taken)
        if empty:
            raise ValueError(
                "from_extras takes nothing from: "
                f"{', '.join(empty)} -- list the packages, or leave the extra out"
            )
        return self


class Output(_Model):
    run: OutputRun
    #: Which of a several-source recipe's releases this output is built from,
    #: named by the project the archive declares.
    #:
    #: Needed only where the output's own name does not say it. `airflow`'s
    #: `apache-airflow-core` output builds the sdist that calls itself
    #: `apache-airflow-core`, so nothing has to be written down; its
    #: `apache-airflow-core-with-all` metapackage corresponds to no upstream
    #: distribution at all, and which release's extras it folds in is a fact
    #: only a maintainer has (DESIGN.md 3.6).
    #:
    #: Ignored on a recipe with one source, where every output draws on it.
    upstream: str | None = None


class RecipeOwned(_Model):
    """Requirement lines that are conda-forge structure, not upstream metadata.

    These are preserved verbatim and never sent through name resolution
    (DESIGN.md 3.3.6). ``functions`` are template expressions matched on the
    *name* position -- ``${{ pin_subpackage(name, exact=True) }}`` is
    structure, while ``pandas >=${{ x }}`` is an ordinary dependency whose
    constraint happens to be templated.

    This is data rather than code so that blessing a new expression is a
    reviewable config commit instead of a release. It is also an **allowlist,
    never a fallback**: an unrecognized template is preserved unchanged but
    gets no provenance, so G1 stops the feedstock with the expression quoted.
    Were it a fallback, every never-upstream dependency would quietly acquire
    provenance and the protection in DESIGN.md 3.3.7 would evaporate.
    """

    functions: tuple[str, ...] = ()
    names: tuple[str, ...] = ()

    def extend(self, other: RecipeOwned | None) -> RecipeOwned:
        """Union with a less specific layer, keeping this layer's order first.

        A family or feedstock *extends* the recognized set rather than
        replacing it -- overriding would silently un-bless `pin_subpackage` for
        the one feedstock that needed to add something local of its own.
        """
        if other is None:
            return self
        return RecipeOwned(
            functions=tuple(dict.fromkeys(self.functions + other.functions)),
            names=tuple(dict.fromkeys(self.names + other.names)),
        )


class AddedLine(_Model):
    """One conda-forge-only requirement, and why the recipe carries it.

    **``reason`` is a required field rather than a YAML comment, and that is a
    decision about what happens when entries get cheap to produce.** While
    every entry is hand-written the comment convention is fine -- somebody
    typing one is already thinking about why. `swage draft` changes that: a
    tool that emits skeletons makes the *typing* free while leaving the
    *thinking* exactly as expensive, and the predictable result is a database
    of entries that silence gates and explain nothing (DESIGN.md 4).

    ``TODO`` and the empty string are refused specifically, because those are
    what a draft ships with. Anything else is accepted: judging whether a
    sentence is a good reason is not the schema's business, and only the
    maintainer can say.

    The same entry serves ``add_requirements`` and ``temporary_requirements``,
    which is why nothing here says which it came from: the two make different
    claims about the *line*, not about its shape (DESIGN.md 3.3.14).
    """

    line: str
    reason: str

    @model_validator(mode="after")
    def _says_why(self) -> AddedLine:
        said = self.reason.strip()
        if not said or said.lower() == "todo":
            raise ValueError(
                f"add_requirements: {self.line!r} needs a reason saying why "
                "conda-forge requires it"
            )
        return self


class OutputAdditions(_Model):
    """What one named output adds, as opposed to every output of the recipe."""

    host: tuple[AddedLine, ...] = ()
    run: tuple[AddedLine, ...] = ()

    def section(self, name: str) -> tuple[AddedLine, ...]:
        return self.run if name == "run" else self.host


class AddRequirements(_Model):
    """conda-forge-only dependencies that upstream never declares.

    Without an entry, a line in the recipe appearing in no upstream version has
    no provenance, fails G1, and stops the feedstock -- deliberately, because
    the alternative is swage deciding on its own whether a maintainer meant it
    (DESIGN.md 3.3.7). With one it is kept for a stated reason.

    Only ``host`` and ``run`` exist, because those are the only sections swage
    plans: ``build`` holds compilers with no relationship to upstream metadata
    (DESIGN.md 3.3.6).

    **An entry is per output as well as per section.** A line frequently
    belongs to exactly one output -- and a section-wide entry would put it on
    all of them, which is how `gdal` would have acquired 48 native libraries in
    each of its 21 outputs. The section-level form stays, because most entries
    really do apply to every output (DESIGN.md 4).

    **This model is both keys.** `temporary_requirements` holds exactly the
    same shape and differs only in the claim it makes about the lines in it,
    the way `temporary_constraints` differs from `constraints` -- so a
    maintainer writing one already knows how to write the other, and swage
    reads them into one list where each entry knows which key it came from
    (DESIGN.md 3.3.14).
    """

    host: tuple[AddedLine, ...] = ()
    run: tuple[AddedLine, ...] = ()
    outputs: dict[str, OutputAdditions] = Field(default_factory=dict)

    def section(self, name: str, output: str = "") -> tuple[AddedLine, ...]:
        """Every entry that applies to ``section`` of ``output``.

        The recipe-wide entries first, so a plan reads them in the order the
        config file states them, and the output's own after.
        """
        wide = self.run if name == "run" else self.host
        per_output = self.outputs.get(output)
        return wide + (per_output.section(name) if per_output is not None else ())


class RunConstraint(_Model):
    """What an existing ``run_constraints`` entry means.

    Nothing in a recipe records which upstream extra -- if any -- an entry came
    from, and inferring it would be the very translation DESIGN.md 3.3.9
    rejects. Written down, a change to the extra's constraint can propagate;
    without it, every entry is left exactly as found and G9 holds the feedstock
    for review.

    ``extra: null`` is a real answer, not a missing one: it says the bound is
    deliberate and tracks nothing upstream.
    """

    extra: str | None = None

    @model_validator(mode="after")
    def _normalized(self) -> RunConstraint:
        if self.extra is not None:
            _check_extras((self.extra,), "run_constraints extra")
        return self


class FamilyMatch(_Model):
    """Which feedstocks belong to a family. ``feedstock`` is an fnmatch glob."""

    feedstock: str


class Quirks(_Model):
    """Settings a family and a feedstock can both carry.

    Feedstock values win over family values, and a family's over the defaults
    (DESIGN.md 4).
    """

    trust: TrustLevel | None = None
    upstream: Upstream | None = None
    extras_as_outputs: ExtrasAsOutputs | None = None
    outputs: dict[str, Output] = Field(default_factory=dict)
    name_map: dict[str, str] = Field(default_factory=dict)
    #: Extends the defaults' allowlist rather than replacing it (DESIGN.md 3.3.6).
    recipe_owned: RecipeOwned | None = None
    add_requirements: AddRequirements | None = None
    #: The same shape, for lines the recipe carries *for now*: a workaround for
    #: somebody else's metadata that must not become permanent by nobody
    #: looking. swage keeps the line, so it is accounted for at G1, and reports
    #: it at every version bump, so it is re-checked at G11 (DESIGN.md 3.3.14).
    temporary_requirements: AddRequirements | None = None
    removals: RemovalPolicy | None = None
    dynamic_dependencies: DynamicPolicy | None = None
    test_matrix: TestMatrixPolicy | None = None
    #: Off everywhere but where somebody turned it on. This is the one
    #: edit swage makes to a version, and the one sha256 it authors rather
    #: than checks, so a feedstock acquires it by decision (DESIGN.md 3.6.4).
    source_versions: SourceVersionPolicy | None = None
    #: conda names whose *unexplained* recipe lines swage may delete rather
    #: than keep (DESIGN.md 3.3.7). Unioned across layers, and it can only ever
    #: reach a line nothing upstream accounts for -- so listing a name here
    #: says "where this line has no upstream basis, it is an artifact", never
    #: "remove this dependency".
    retire: tuple[str, ...] = ()
    #: conda package name -> what its `run_constraints` entry tracks. An entry
    #: with no association here fails G9 (DESIGN.md 3.3.9).
    run_constraints: dict[str, RunConstraint] = Field(default_factory=dict)
    #: conda package name -> a bound this feedstock adds to an ordinary
    #: dependency line, beyond what upstream declares, e.g.
    #: ``apache-airflow: "<3.1.3"``. Without an entry, a recipe stating one
    #: fails G11 and swage would drop it (DESIGN.md 3.3.14).
    #:
    #: **Not `run_constraints`**, which is about the recipe's
    #: `run_constraints` *section* -- a bound imposed on whoever happens to
    #: have the package in the same environment. This one tightens a dependency
    #: the package installs.
    constraints: dict[str, Override] = Field(default_factory=dict)
    #: The same, for a bound that is **not** meant to outlive the reason it
    #: was added for. swage keeps it exactly as `constraints` does and holds
    #: the feedstock at every update, so somebody re-checks whether the
    #: workaround is still needed (DESIGN.md 3.3.14).
    temporary_constraints: dict[str, Override] = Field(default_factory=dict)
    #: An empty list means "declared, adds nothing", which is materially
    #: different from the key being absent (DESIGN.md 4).
    embedded_extras: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_answer_per_name(self) -> Quirks:
        """A bound is permanent or temporary, never both.

        The two say opposite things about the same name -- one that the bound
        outlives its reason and one that it must be re-checked -- and nothing
        decides which wins.
        """
        both = sorted(set(self.constraints) & set(self.temporary_constraints))
        if both:
            raise ValueError(
                "listed in both 'constraints' and 'temporary_constraints': "
                f"{', '.join(both)}"
            )
        return self

    @model_validator(mode="after")
    def _embedded_extra_keys(self) -> Quirks:
        """Keys are looked up by `UpstreamRequirement.key`, so they must match it."""
        for key in self.embedded_extras:
            try:
                requirement = Requirement(key)
            except InvalidRequirement as exc:
                raise ValueError(
                    f"embedded_extras: {key!r} is not a requirement: {exc}"
                ) from exc
            if not requirement.extras:
                raise ValueError(
                    f"embedded_extras: {key!r} names no extra; the key is a "
                    "requirement carrying one, like 'pyhive[hive-pure-sasl]'"
                )
            _check_extras(tuple(sorted(requirement.extras)), f"embedded_extras {key!r}")
        return self


class Defaults(_Model):
    """``config/defaults.yaml`` -- global policy.

    ``trust`` is required rather than defaulted because the bottom of the trust
    ladder should be stated out loud; new feedstocks take this file's. The
    global name map lives in its own file, not here.
    """

    trust: TrustLevel
    #: Required rather than defaulted, for the same reason `trust` is. The
    #: allowlist is load-bearing: without `python` and `pip` on it, G1 blocks
    #: every feedstock in the fleet, and it should be stated in the file rather
    #: than hidden in code where a config commit cannot reach it.
    recipe_owned: RecipeOwned
    #: What `host` is built with where upstream declares no `[build-system]`
    #: at all. PEP 517 makes setuptools the implicit backend and conda-forge
    #: follows it, since the recipe still needs something to build with.
    #:
    #: Strictly a backup for silence. A project that names hatchling or
    #: poetry-core gets what it asked for, so swage never overrides a
    #: maintainer here -- and across the fleet every recipe whose upstream
    #: says nothing already lists exactly this. Written down rather than
    #: hardcoded so that changing it is a reviewable config commit.
    default_build_requires: tuple[str, ...] = ("setuptools",)
    #: Build requirements a cross build takes from the host prefix, so a
    #: recipe never repeats them in `build` (DESIGN.md 3.3.6.1).
    #:
    #: An allowlist, and empty by default for the same reason every other one
    #: here is restrictive: a name nobody has checked holds the feedstock,
    #: which is a stop rather than a recipe that fails cross-compiled.
    pure_python_build_tools: tuple[str, ...] = ()
    #: Defaulted rather than required, unlike `trust` and `recipe_owned`,
    #: because the safe value is the restrictive one -- a missing policy holds
    #: work for review rather than releasing it.
    removals: RemovalPolicy = "review"
    dynamic_dependencies: DynamicPolicy = "review"
    test_matrix: TestMatrixPolicy = "review"
    source_versions: SourceVersionPolicy = "never"


class Family(Quirks):
    """``config/families/<name>.yaml``."""

    family: str
    match: FamilyMatch


class Feedstock(Quirks):
    """``config/feedstocks/<name>.yaml``."""

    feedstock: str
    family: str | None = None
