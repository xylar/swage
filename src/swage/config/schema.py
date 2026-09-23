"""Pydantic schema for the quirks database (v1 §4; DESIGN.md §5).

Every layer is validated with ``extra="forbid"``, so a mistyped key is a startup
error. The keys' meanings are `docs/configuration.md`'s.
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
    "EntryPointsPolicy",
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

#: ``never`` writes nothing; ``propose`` pushes and leaves the label to a
#: person; ``auto`` labels too (v1 §5.4). ``never`` rather than ``off``, which
#: YAML 1.1 reads as a boolean. Whether swage pushes is the findings' answer,
#: not this key's.
TrustLevel = Literal["never", "propose", "auto"]

#: Whether an upstream-dropped removal may merge unattended (DESIGN.md §5.3; v1
#: §3.3.8).
RemovalPolicy = Literal["review", "auto"]

#: Whether a dependency list upstream computed at build time may merge
#: unattended (DESIGN.md §5.3; v1 §3.6.3).
DynamicPolicy = Literal["review", "auto"]

#: Whether a recipe whose python test matrix swage completed may merge
#: unattended (DESIGN.md §5.3; v1 §3.7).
TestMatrixPolicy = Literal["review", "auto"]
#: Whether swage keeps an output's `build.python.entry_points` in step with
#: upstream (DESIGN.md §9.6); `manual` says the list is conda-forge's own.
EntryPointsPolicy = Literal["reconcile", "manual"]
#: Whether swage may set the version a second source is pinned at (DESIGN.md
#: §5.3; v1 §3.6.5).
SourceVersionPolicy = Literal["review", "auto"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _check_extras(names: tuple[str, ...], field: str) -> None:
    """Refuse an extra spelled any way but the one swage reads it as: swage
    PEP 685-normalizes every extra name, and a stale spelling would silently
    never match.
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
    """Metadata read from the archive the recipe's ``source.url`` pins, from
    PyPI or anywhere else.
    """

    source: Literal["archive"]
    #: Where inside the archive the metadata is, relative to its single
    #: top-level directory, so the path survives a version bump. Only needed
    #: where the file at the root is not the right one.
    metadata: str | None = None


class NoUpstream(_Model):
    """This feedstock packages no python distribution, and declares nothing.

    Not for a feedstock whose declaration swage merely cannot read: that is
    `ManualUpstream`, which says where to look (DESIGN.md §1). ``reason``
    says what the feedstock does package.
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
    """Dependencies read out of ESMF's makefile and the feedstock's build
    script (v1 §3.6.6): a reader for one feedstock, with nothing to
    configure.
    """

    source: Literal["esmf"]


class CMakeUpstream(_Model):
    """Dependencies read out of the project's top-level `CMakeLists.txt`
    (v1 §3.6.7).

    ``find_package(X)`` without `REQUIRED` is a packaging decision, so
    ``supported`` says this build takes it and ``skip`` says it does not.
    Names are matched without regard to case, as `cmake-map.yaml` is.
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
    """swage does not read this declaration, and says where it is (v1 §3.6.8).

    ``declares`` names the files, relative to the archive's top-level
    directory. They are reported, written into `swage draft`'s workbench, and
    compared against the previous release where swage has it. A path that is
    not in the archive stops the feedstock.
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
    declares (G3).
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
    """A dependency conda-forge does not have, that this feedstock ships without
    (v1 §3.2.3).

    A decision, recorded with its ``reason``. An entry for a name that
    resolves is an error, so one cannot outlive conda-forge packaging the
    thing.
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
    """A bound this feedstock states that upstream does not, and why
    (v1 §3.3.14). ``bound`` is intersected with what upstream declares.
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
    #: split across several outputs (v1 §4). Drawn on exactly as one in `extras`
    #: is; a package no output takes is reported.
    from_extras: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    #: Upstream extras this output deliberately does not fold in, and what opts
    #: the feedstock into exhaustiveness (G3, v1 §4).
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
    #: named by the project the archive declares (v1 §3.6). Needed only where
    #: the output's own name does not say it; ignored on a one-source recipe.
    upstream: str | None = None


class RecipeOwned(_Model):
    """Requirement lines that are conda-forge structure, not upstream metadata
    (DESIGN.md §9.4; v1 §3.3.6).

    ``functions`` are template calls in the name position; ``variables`` are
    the bare interpolations a build variant leaves there. An allowlist,
    never a fallback: an unrecognized template is preserved and unexplained.
    """

    functions: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    #: Context variables a recipe may name a whole dependency with, such as
    #: ``${{ mpi }}``. Apart from `names` because the two are matched against
    #: different things.
    variables: tuple[str, ...] = ()

    def extend(self, other: RecipeOwned | None) -> RecipeOwned:
        """Union with a less specific layer, keeping this layer's order first: a
        feedstock extends the recognized set rather than replacing it.
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

    ``reason`` is required, and ``TODO`` and the empty string are refused,
    because those are what a draft ships with (v1 §4). The same entry serves
    ``add_requirements`` and ``temporary_requirements`` (v1 §3.3.14).
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
    """conda-forge-only dependencies that upstream never declares (v1 §3.3.7).

    Only ``host`` and ``run``, the sections swage plans. Recipe-wide, and per
    output where a line belongs to one (v1 §4). `temporary_requirements`
    holds the same shape and differs in the claim it makes (v1 §3.3.14).
    """

    host: tuple[AddedLine, ...] = ()
    run: tuple[AddedLine, ...] = ()
    outputs: dict[str, OutputAdditions] = Field(default_factory=dict)

    def section(self, name: str, output: str = "") -> tuple[AddedLine, ...]:
        """Every entry that applies to ``section`` of ``output``, recipe-wide
        entries first.
        """
        wide = self.run if name == "run" else self.host
        per_output = self.outputs.get(output)
        return wide + (per_output.section(name) if per_output is not None else ())


class RunConstraint(_Model):
    """What an existing ``run_constraints`` entry means (v1 §3.3.9).

    ``extra: null`` is a real answer: the bound is deliberate and tracks
    nothing upstream, so swage has nothing to say about it. ``extra: <name>``
    is the other kind, and swage keeps saying so until ``keep`` records the
    decision to leave it (DESIGN.md §9.7).
    """

    extra: str | None = None
    #: Why an entry transcribed from an extra is staying. Set means the
    #: decision has been made and swage stops reporting it.
    keep: str | None = None

    @model_validator(mode="after")
    def _normalized(self) -> RunConstraint:
        if self.extra is not None:
            _check_extras((self.extra,), "run_constraints extra")
        return self


class BuiltEverywhere(_Model):
    """A dependency whose platform marker is about upstream's wheel matrix
        (v1 §3.3.4.1).

    It excuses the platform and machine axes only.
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
    """An ``if:`` that selects a build variant rather than narrowing upstream
    (v1 §3.3.4).

    The entry inside it is preserved as written and explained by upstream's
    unconditional declaration. ``condition`` is matched against the recipe's
    text, whitespace normalized. ``packages`` names the packages the entry
    decides about, which are the ones swage plans a requirement for.
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
    """Settings a family and a feedstock can both carry, most specific winning
    (v1 §4).
    """

    trust: TrustLevel | None = None
    upstream: Upstream | None = None
    extras_as_outputs: ExtrasAsOutputs | None = None
    outputs: dict[str, Output] = Field(default_factory=dict)
    name_map: dict[str, str] = Field(default_factory=dict)
    #: Extends the defaults' allowlist rather than replacing it (design-v1.md 3.3.6).
    recipe_owned: RecipeOwned | None = None
    add_requirements: AddRequirements | None = None
    #: The same shape, for lines the recipe carries for now: accounted for at G1
    #: and re-asked at G11 (v1 §3.3.14).
    temporary_requirements: AddRequirements | None = None
    removals: RemovalPolicy | None = None
    dynamic_dependencies: DynamicPolicy | None = None
    test_matrix: TestMatrixPolicy | None = None
    entry_points: EntryPointsPolicy | None = None
    #: Off everywhere but where somebody turned it on (v1 §3.6.5).
    source_versions: SourceVersionPolicy | None = None
    #: ``if:`` conditions that select a conda-forge build variant (v1 §3.3.4).
    #: Unioned across layers.
    variant_conditions: tuple[VariantCondition, ...] = ()
    #: conda names whose unexplained recipe lines swage may delete rather than
    #: keep (v1 §3.3.7). Unioned across layers; never "remove this dependency".
    retire: tuple[str, ...] = ()
    #: conda package name -> why upstream's platform or machine marker for it
    #: describes its wheel matrix (v1 §3.3.4.1). Merged most-specific-wins.
    built_everywhere: dict[str, BuiltEverywhere] = Field(default_factory=dict)
    #: conda package name -> what its `run_constraints` entry tracks. An entry
    #: with no association here fails G9 (design-v1.md 3.3.9).
    run_constraints: dict[str, RunConstraint] = Field(default_factory=dict)
    #: conda package name -> a bound this feedstock adds beyond what upstream
    #: declares (v1 §3.3.14). Not `run_constraints`, which is a different
    #: section.
    constraints: dict[str, Override] = Field(default_factory=dict)
    #: The same, for a bound not meant to outlive its reason: re-asked at every
    #: update (v1 §3.3.14).
    temporary_constraints: dict[str, Override] = Field(default_factory=dict)
    #: conda package name -> the bound one noarch package states where
    #: upstream's own declarations contradict each other (v1 §3.3.2). Replaces
    #: upstream's bounds rather than tightening them, and is re-asked at every
    #: update.
    overruled_constraints: dict[str, Override] = Field(default_factory=dict)
    #: Upstream name -> why conda-forge has no such package (v1 §3.2.3). Merged
    #: most-specific-wins.
    not_packaged: dict[str, NotPackaged] = Field(default_factory=dict)
    #: An empty list means "declared, adds nothing", which is materially
    #: different from the key being absent (design-v1.md 4).
    embedded_extras: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_answer_per_name(self) -> Quirks:
        """A bound is permanent, temporary or overruling, never two of them."""
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
    """``config/defaults.yaml``: global policy. ``trust`` is required so the
    bottom of the ladder is stated out loud.
    """

    trust: TrustLevel
    #: Required rather than defaulted: the allowlist is load-bearing and belongs
    #: in the file.
    recipe_owned: RecipeOwned
    #: What `host` is built with where upstream declares no `[build-system]`:
    #: PEP 517's implicit setuptools. A backup for silence, never an override.
    default_build_requires: tuple[str, ...] = ("setuptools",)
    #: Build requirements a cross build takes from the host prefix (v1
    #: §3.3.6.1). An allowlist, empty by default.
    pure_python_build_tools: tuple[str, ...] = ()
    #: Defaulted rather than required, because the safe value is the restrictive
    #: one.
    removals: RemovalPolicy = "review"
    dynamic_dependencies: DynamicPolicy = "review"
    test_matrix: TestMatrixPolicy = "review"
    entry_points: EntryPointsPolicy = "reconcile"
    source_versions: SourceVersionPolicy = "review"


class Family(Quirks):
    """``config/families/<name>.yaml``."""

    family: str
    match: FamilyMatch

    @model_validator(mode="after")
    def _states_no_rung(self) -> Family:
        """A glob may not decide what merges unattended (v1 §5.4): what it decides,
        it decides for members nobody has added yet. `never` is refused too.
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
    #: Why nobody maintains this feedstock any more. The decision where GitHub
    #: does not carry it yet (v1 §3.4.1); per feedstock, never per family.
    unmaintained: str | None = None


class TrustBatch(_Model):
    """A group of feedstocks put on one rung together, and the argument for it
    (v1 §5.4). ``reason`` is required, for the reason `AddedLine.reason` is.
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
    """``config/trust.yaml``: the rung, for a feedstock with nothing else to
    say.

        Keyed by the rung, so the whole set that may merge unattended is one
        thing to read. ``propose`` is absent because it is the floor.
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
