"""Pydantic schema for the quirks database (DESIGN.md 4).

Every layer of the database is validated against these models with
``extra="forbid"``, so a mistyped key is a startup error rather than a silently
ignored setting.
"""

from __future__ import annotations

from itertools import combinations
from pathlib import PurePosixPath
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
    "EsmfUpstream",
    "ExtrasAsOutputs",
    "Family",
    "Feedstock",
    "GitHubUpstream",
    "NotPackaged",
    "Output",
    "OutputRun",
    "Override",
    "Quirks",
    "RecipeOwned",
    "RemovalPolicy",
    "RunConstraint",
    "SourceVersionPolicy",
    "TestMatrixPolicy",
    "TrustBatch",
    "TrustLevel",
    "TrustList",
    "Upstream",
    "VariantCondition",
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
#: rest of the recipe requires one it does not build (DESIGN.md 3.6.5).
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


class NoUpstream(_Model):
    """This feedstock packages no python distribution, and declares nothing.

    Without this, swage does not conclude there is nothing to read: it reads
    whatever python metadata the source archive happens to contain.
    `e3sm-tools` installs Fortran binaries and two scripts, and the E3SM
    archive's only `pyproject.toml` describes `pyscream` -- a component of the
    model this feedstock does not install -- so the plan proposed `pyscream`'s
    `mpi4py` for a recipe with no use for it.

    **Not for a feedstock whose declaration swage merely cannot read yet.**
    That is a gap in what swage can read rather than a boundary on what it
    covers (DESIGN.md, "The model, in one page"), and the two want opposite
    entries: this one says there is nothing to look at, where a maintainer
    coming back to the feedstock needed to be told where to look. `esmf` is the
    distinction made concrete -- its dependencies are in `build/common.mk` and
    it has a reader of its own (DESIGN.md 3.6.6). What is left here is the
    feedstock with no file anybody could point a reader at: two scripts whose
    import statements are the declaration.

    ``reason`` says what the feedstock does package, in words a person reading
    `config/` can check.
    """

    source: Literal["none"]
    reason: str

    @model_validator(mode="after")
    def _says_what_it_packages(self) -> NoUpstream:
        said = self.reason.strip()
        if not said or said.lower() == "todo":
            raise ValueError(
                "upstream: source 'none' needs a reason saying what this "
                "feedstock packages instead"
            )
        return self


class EsmfUpstream(_Model):
    """Dependencies read out of ESMF's makefile and the feedstock's build script.

    Named for the project rather than for the build system, because that is
    what it is: a reader for one feedstock, whose rules are ESMF's own. A
    makefile is not a metadata format and there is no generic makefile reader
    to be had -- what `build/common.mk` states, and that `recipe/build.sh`
    decides which of it applies, are facts about ESMF (DESIGN.md 3.6.6).

    Nothing to configure. Where the files are is part of what the reader
    knows, and a key naming them would invite a second feedstock to point this
    reader at a makefile it was never written for.
    """

    source: Literal["esmf"]


class CMakeUpstream(_Model):
    """Dependencies read out of the project's top-level `CMakeLists.txt`.

    Named for the build system rather than for a project, unlike
    `EsmfUpstream`, and that difference is the point: `find_package(SQLite3
    REQUIRED)` means the same thing in every CMake project there is, where a
    makefile is not a metadata format and ESMF's rules are ESMF's alone
    (DESIGN.md 3.6.7).

    Where the file is needs no configuring: CMake decides that -- `CMakeLists.txt`
    at the top of the source tree -- and the `-D` flags that say which of its
    `option(...)` blocks are on are in the feedstock's own build script,
    already beside the recipe.

    What does need answering is the optional half of the declaration.
    ``find_package(X)`` without `REQUIRED` is upstream saying the project
    builds either way, so whether conda-forge builds against X is a packaging
    decision no file upstream contains -- the same shape as an upstream extra
    and the same two keys. ``supported`` says this build takes it, which makes
    it a requirement the recipe's `host` is reconciled against; ``skip`` says
    it does not, which is how "considered and declined" gets on the record.

    **This is what brings the netcdf family into reach.** `netcdf-fortran` and
    `netcdf-cxx4` write `FIND_PACKAGE(netCDF QUIET)` and then fall back to a
    `FIND_LIBRARY` with a `FATAL_ERROR` behind it, so upstream requires netCDF
    and never says `REQUIRED`. Read by the reader alone those are optional, and
    `libnetcdf` in their recipes is a line nothing explains.

    Names are `find_package` names and are matched **without regard to case**,
    for the reason `cmake-map.yaml` is: `netcdf-fortran` writes `netCDF` and
    `cprnc` writes `NetCDF`, meaning the same package.
    """

    source: Literal["cmake"]
    #: Optional `find_package` names this build takes.
    supported: tuple[str, ...] = ()
    #: Optional `find_package` names this build deliberately does not take.
    skip: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _disjoint(self) -> CMakeUpstream:
        both = sorted(
            {name.lower() for name in self.supported}
            & {name.lower() for name in self.skip}
        )
        if both:
            raise ValueError(
                "upstream: find_package names listed in both 'supported' and "
                f"'skip': {', '.join(both)}"
            )
        return self


class ManualUpstream(_Model):
    """swage does not read this declaration, and says where it is.

    The fallback where a reader is not available, and deliberately a different
    answer from `NoUpstream`. That one says there is nothing to read; this one
    says there is something, names it, and admits swage cannot parse it. Both
    stop before planning, so neither ever proposes a line -- and the difference
    is what a maintainer coming back to the feedstock is told.

    **autotools is what this exists for** (DESIGN.md 3.6.8). `AC_CHECK_LIB` is
    a probe rather than a declaration, and the calls that are declarations are
    macros each project defines in its own `m4/` directory -- `tempest-remap`
    writes `ACX_NETCDF`, `ncview` writes `AC_PATH_NETCDF`, and both mean
    libnetcdf. There is no vocabulary to write a reader against, but there is
    always a file, and which file it is does not change between releases.

    ``declares`` names those files, relative to the archive's top-level
    directory, the same way `ArchiveUpstream.metadata` is. They are reported,
    written into `swage draft`'s workbench, and -- where swage has the release
    the recipe is moving from -- compared against it, so a version bump says
    which of them changed. That last part needs no vocabulary at all: it is a
    text comparison, and "this file moved" is the honest form of "your
    dependencies may have changed".

    A path that is not in the archive stops the feedstock. An entry naming a
    file upstream has since moved or renamed is exactly the case where silence
    would be worst -- the declaration would have gone somewhere else and swage
    would still be pointing at where it used to be.
    """

    source: Literal["manual"]
    #: The files upstream states its dependencies in, in the order a reader
    #: should open them: the entry point first, then what it pulls in.
    declares: tuple[str, ...]
    #: Why swage does not read them, in words somebody can check against the
    #: files themselves.
    reason: str

    @model_validator(mode="after")
    def _names_something(self) -> ManualUpstream:
        if not self.declares:
            raise ValueError(
                "upstream: source 'manual' needs at least one file in "
                "'declares' -- naming none is what source 'none' is for"
            )
        for path in self.declares:
            if path.startswith("/") or ".." in PurePosixPath(path).parts:
                raise ValueError(
                    f"upstream: declares {path!r} is not inside the archive; "
                    "paths are relative to its top-level directory"
                )
        said = self.reason.strip()
        if not said or said.lower() == "todo":
            raise ValueError(
                "upstream: source 'manual' needs a reason saying why swage "
                "does not read these files"
            )
        return self


Upstream = Annotated[
    GitHubUpstream
    | ArchiveUpstream
    | NoUpstream
    | EsmfUpstream
    | CMakeUpstream
    | ManualUpstream,
    Field(discriminator="source"),
]


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


class NotPackaged(_Model):
    """A dependency conda-forge does not have, that this feedstock ships without.

    Not every name upstream declares is obtainable. `apache-beam`'s `yaml`
    extra requires `quickjs-ng`, the python binding for a javascript engine,
    and conda-forge packages no such thing -- so swage cannot write the line
    and has nothing to say about the fact except that a name failed to
    resolve, which stops the feedstock (G2).

    **Shipping without it is a decision, and it belongs beside the others.**
    It is the same act as declining an extra with `skip` or a line with
    `retire`: somebody weighed what the package promises without the
    dependency and decided it was still worth publishing. `reason` is where a
    later reader finds out what they weighed -- for `quickjs-ng`, that Beam
    imports it under `try`/`except` and raises a clear error naming it if a
    pipeline actually uses a javascript UDF.

    **It is not a way to drop a dependency that does exist.** swage checks:
    an entry for a name that resolves is an error, because the entry is what
    keeps the dependency out of the recipe and there is no longer a reason for
    it to. That is what stops one outliving conda-forge packaging the thing.
    """

    reason: str

    @model_validator(mode="after")
    def _says_why(self) -> NotPackaged:
        said = self.reason.strip()
        if not said or said.lower() == "todo":
            raise ValueError(
                "needs a reason saying what this package promises without the "
                "dependency, and why that is still worth publishing"
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
    constraint happens to be templated. ``variables`` are the bare
    interpolations a build variant leaves in that position, ``${{ mpi }}``
    being the whole of it in this fleet.

    This is data rather than code so that blessing a new expression is a
    reviewable config commit instead of a release. It is also an **allowlist,
    never a fallback**: an unrecognized template is preserved unchanged but
    gets no provenance, so G1 stops the feedstock with the expression quoted.
    Were it a fallback, every never-upstream dependency would quietly acquire
    provenance and the protection in DESIGN.md 3.3.7 would evaporate.
    """

    functions: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    #: Context variables a recipe may name a whole dependency with:
    #: ``${{ mpi }}`` is `mpich`, `openmpi` or `nompi` depending on which
    #: variant is building, and no other spelling exists for it.
    #:
    #: A third list rather than an entry in `names`, because the two are
    #: matched against different things -- `names` holds package names and
    #: this holds variant keys -- and a variant key that happened to collide
    #: with a package name would otherwise bless both.
    variables: tuple[str, ...] = ()

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
            variables=tuple(dict.fromkeys(self.variables + other.variables)),
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


class BuiltEverywhere(_Model):
    """A dependency whose platform marker is about upstream's wheel matrix.

    Upstream gates a dependency on the machines and platforms it publishes
    wheels for, which is a statement about what `pip install` can get hold of
    rather than about where the dependency is needed. conda-forge builds its
    own packages for every target it supports, so the gate says nothing there
    and swage would otherwise stop on a marker naming an axis a `noarch: python`
    artifact does not vary over (DESIGN.md 3.3.4.1).

    `sqlalchemy` is the shape: upstream declares
    ``greenlet>=1; platform_machine == "aarch64" or ...``, enumerating the
    machines its own wheels cover, and conda-forge's greenlet is built for all
    of them and more.

    An entry is a judgment about what the package promises, which is why it is
    written down rather than inferred: shipping the dependency everywhere is
    usually the right call and is still a decision, and the reason is what a
    later reader checks it against.

    **It excuses the platform and machine axes only.** A marker naming anything
    else -- an operating system release, an interpreter build -- is refused as
    before, because this says where conda-forge builds and nothing more.
    """

    reason: str

    @model_validator(mode="after")
    def _says_why(self) -> BuiltEverywhere:
        said = self.reason.strip()
        if not said or said.lower() == "todo":
            raise ValueError(
                "needs a reason saying which of conda-forge's builds carry "
                "this package, and why upstream's marker leaves some out"
            )
        return self


class VariantCondition(_Model):
    """An ``if:`` that selects a build variant rather than narrowing upstream.

    A recipe stating a dependency only under a condition, where upstream
    declares it always, is a recipe missing that dependency everywhere else --
    so swage refuses to flatten the condition away and holds the feedstock
    (DESIGN.md 3.3.4). That rule cannot see the one case where the condition
    is conda-forge's own: `esmf` states `parallelio` under
    ``mpi != "nompi"`` because conda-forge builds it once per mpi
    implementation and ESMF turns PIO on only for the mpi builds. Upstream
    declares the dependency unconditionally *for the builds that have it*,
    and there is no way to say that in a PEP 508 marker or a `common.mk`
    toggle -- the variant axis is conda-forge's, and only a maintainer knows
    which of its conditions are on it.

    An entry says this condition is one of those. The entry inside it is
    preserved exactly as written and explained by upstream's unconditional
    declaration, rather than replaced by a line with the condition gone.

    ``condition`` is matched against the recipe's own text, whitespace
    normalized. Nothing is evaluated: this is one condition a maintainer
    blessed, not an expression language.

    **``packages`` is what the entry is about, and it is required.** A
    condition on its own would bless whatever upstream-declared dependency
    happened to sit inside it, anywhere in the recipe -- so moving an
    unrelated package into `esmf`'s `mpi != "nompi"` block would be accepted
    silently, which is the drift the refusal exists to catch. It also left the
    entry unreadable as config: a maintainer reviewing `config/` could see the
    condition and not which lines it decided about. Naming them fixes both,
    and it is what every other allowlist in this database already does.

    **It is not a list of what the conditional entry contains.** swage keeps
    that entry exactly as the recipe writes it and never decides what goes
    inside; what this list decides is whether the entry *survives*. So it
    holds the packages swage plans a requirement for, which are the only ones
    whose condition is at risk of being flattened away.

    `esmf`'s block also holds `${{ mpi }}`, and leaving it out is not a claim
    that ESMF has no MPI dependency -- it has one and says so with
    `ESMF_COMM`, and `${{ mpi }}` is a real package, whichever of `mpich`,
    `openmpi` and `nompi` the variant builds against. It is out because
    `build/common.mk` states no libraries under `ESMF_COMM`, so nothing plans
    a line for it and there is nothing about it to decide. It stays inside the
    block because the recipe put it there.
    """

    condition: str
    packages: tuple[str, ...]
    reason: str

    @model_validator(mode="after")
    def _says_why(self) -> VariantCondition:
        if not self.condition.strip():
            raise ValueError("a condition that says nothing matches nothing")
        if not self.packages:
            raise ValueError(
                f"{self.condition!r} blesses no package -- list the ones "
                "upstream declares unconditionally and this condition wraps, "
                "or drop the entry"
            )
        said = self.reason.strip()
        if not said or said.lower() == "todo":
            raise ValueError(
                f"{self.condition!r} needs a reason saying why this condition "
                "is conda-forge's build variant rather than a narrowing of "
                "what upstream declares"
            )
        return self

    def covers(self, package: str) -> bool:
        return package in self.packages


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
    #: than checks, so a feedstock acquires it by decision (DESIGN.md 3.6.5).
    source_versions: SourceVersionPolicy | None = None
    #: ``if:`` conditions that select a conda-forge build variant, so a line
    #: inside one is explained by an unconditional upstream declaration rather
    #: than refused (DESIGN.md 3.3.4). Unioned across layers: a family blesses
    #: what its whole family builds and a feedstock adds its own.
    variant_conditions: tuple[VariantCondition, ...] = ()
    #: conda names whose *unexplained* recipe lines swage may delete rather
    #: than keep (DESIGN.md 3.3.7). Unioned across layers, and it can only ever
    #: reach a line nothing upstream accounts for -- so listing a name here
    #: says "where this line has no upstream basis, it is an artifact", never
    #: "remove this dependency".
    retire: tuple[str, ...] = ()
    #: conda package name -> why upstream's platform or machine marker for it
    #: describes upstream's wheel matrix rather than where it is needed, so
    #: swage takes that half of the marker as true (DESIGN.md 3.3.4.1). Merged
    #: most-specific-wins: an entry is a statement about one dependency.
    built_everywhere: dict[str, BuiltEverywhere] = Field(default_factory=dict)
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
    #: conda package name -> the bound one noarch package states where
    #: upstream's own declarations of it contradict each other across the
    #: pythons that package is installed on (DESIGN.md 3.3.2).
    #:
    #: **This one replaces upstream's bounds rather than tightening them**,
    #: which is why it is a third key and not a flag on either above: those
    #: two intersect with what upstream declares, and here there is nothing
    #: coherent to intersect with. It applies only where a contradiction
    #: actually arises, and swage re-asks about every entry at every update
    #: exactly as `temporary_constraints` does -- overruling upstream is
    #: provisional by nature, so there is no permanent half to this key.
    overruled_constraints: dict[str, Override] = Field(default_factory=dict)
    #: Upstream name -> why conda-forge has no such package and this feedstock
    #: ships without it (DESIGN.md 3.2.3). Merged most-specific-wins: an entry
    #: is a statement about one dependency.
    not_packaged: dict[str, NotPackaged] = Field(default_factory=dict)
    #: An empty list means "declared, adds nothing", which is materially
    #: different from the key being absent (DESIGN.md 4).
    embedded_extras: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_answer_per_name(self) -> Quirks:
        """A bound is permanent, temporary or overruling -- never two of them.

        The three say different things about the same name: that the bound
        outlives its reason, that it must be re-checked, and that it stands in
        for declarations upstream cannot make agree. Nothing decides which
        wins, and the third does not even combine with the others -- it
        replaces upstream's bounds where they intersect to nothing.
        """
        keys = (
            ("constraints", self.constraints),
            ("temporary_constraints", self.temporary_constraints),
            ("overruled_constraints", self.overruled_constraints),
        )
        for (first, one), (second, other) in combinations(keys, 2):
            both = sorted(set(one) & set(other))
            if both:
                raise ValueError(
                    f"listed in both {first!r} and {second!r}: {', '.join(both)}"
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

    @model_validator(mode="after")
    def _states_no_rung(self) -> Family:
        """A glob may not decide what merges unattended (DESIGN.md 5.4).

        Two families granted `auto` to everything they matched, and the
        argument for it was that copying one conclusion into fifty
        per-feedstock files would deny the premise the family is built on.
        That was true, and `config/trust.yaml` answers it: fifty names in one
        batch under one reason, which is the same economy without the property
        that makes a glob wrong.

        What a glob decides, it decides for members nobody has added yet. The
        fifty-first `google-cloud-*` feedstock would arrive already blessed,
        having never been read by anything -- and the direction of that
        mistake is the one nobody notices, because it looks exactly like a
        feedstock that has been fine all along.

        A rung is refused here rather than only `auto`, because the same
        argument covers `never`: a family blanket-refusing every future member
        silences a feedstock nobody has looked at, which is safer and still
        not a decision a glob should be making.
        """
        if self.trust is not None:
            raise ValueError(
                f"a family cannot set a trust rung: '{self.family}' is a glob, "
                "and would decide for feedstocks nobody has added yet. Name the "
                "feedstocks in config/trust.yaml"
            )
        return self


class Feedstock(Quirks):
    """``config/feedstocks/<name>.yaml``."""

    feedstock: str
    family: str | None = None
    #: Why nobody maintains this feedstock any more, in a sentence somebody
    #: can check. swage reads no further and proposes nothing.
    #:
    #: **The decision, where GitHub does not carry it yet.** An archived
    #: feedstock is read-only and swage learns that from GitHub itself, which
    #: needs no entry here (DESIGN.md 3.4.1). This is the gap before that:
    #: archiving a conda-forge feedstock is a request somebody else merges,
    #: and until they do, the repository still accepts writes and looks
    #: exactly like a live one.
    #:
    #: Per-feedstock and never per-family, because a family entry would retire
    #: feedstocks added to it afterwards -- silently, and in the direction of
    #: doing nothing, which is the direction nobody notices.
    unmaintained: str | None = None


class TrustBatch(_Model):
    """A group of feedstocks put on one rung together, and the argument for it.

    **The batch is the unit because the reason is.** A rung granted feedstock
    by feedstock carries evidence about that feedstock -- an update watched
    through to a green build, a maintainer who is the only person affected --
    and that belongs in the feedstock's own file. A hundred feedstocks
    promoted at once are promoted for one reason, and writing that reason a
    hundred times would say less than writing it once: a hundred copies of a
    sentence is a sentence nobody checked.

    ``reason`` is a required field rather than a comment for the same reason
    `AddedLine.reason` is. This is the list that ends in swage merging other
    people's pull requests unattended, and a list of bare names is one nobody
    can audit a year later -- the names would be there and the argument would
    not.
    """

    reason: str
    feedstocks: tuple[str, ...]

    @model_validator(mode="after")
    def _says_why(self) -> TrustBatch:
        if not self.feedstocks:
            raise ValueError("a batch with no feedstocks in it decides nothing")
        said = self.reason.strip()
        if not said or said.lower() == "todo":
            raise ValueError(
                f"{self.feedstocks[0]!r} and the rest of its batch need a reason "
                "saying what earned this rung"
            )
        return self


class TrustList(_Model):
    """``config/trust.yaml`` -- the rung, for a feedstock with nothing else to say.

    A rung is a standing decision about one feedstock, so it used to be
    written in that feedstock's own file -- which meant a file whose entire
    content was a name and a rung, teaching swage nothing. That is friction
    without a record: the commit granting it is the audit trail either way,
    and it reads better as one batch of names than as a hundred files.

    Keyed by the rung rather than by the feedstock, so the whole set that may
    merge unattended is one thing to read. ``propose`` is absent because it is
    the floor: a feedstock a family has promoted and that should not have been
    is demoted in its own file, where somebody looking at that feedstock will
    find out why.
    """

    auto: tuple[TrustBatch, ...] = ()
    never: tuple[TrustBatch, ...] = ()

    @property
    def rungs(self) -> dict[str, TrustLevel]:
        """Feedstock -> the rung this file puts it on."""
        entries: dict[str, TrustLevel] = {}
        for batch in self.never:
            entries.update(dict.fromkeys(batch.feedstocks, "never"))
        for batch in self.auto:
            entries.update(dict.fromkeys(batch.feedstocks, "auto"))
        return entries

    @model_validator(mode="after")
    def _decided_once(self) -> TrustList:
        counted: dict[str, int] = {}
        for batches in (self.auto, self.never):
            for batch in batches:
                for name in batch.feedstocks:
                    counted[name] = counted.get(name, 0) + 1
        repeated = sorted(name for name, count in counted.items() if count > 1)
        if repeated:
            listed = ", ".join(repeated)
            raise ValueError(
                f"listed more than once, so what this file says about it "
                f"depends on the order it is read in: {listed}"
            )
        return self
