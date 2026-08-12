"""Pydantic schema for the quirks database (DESIGN.md 4).

Every layer of the database is validated against these models with
``extra="forbid"``, so a mistyped key is a startup error rather than a silently
ignored setting.
"""

from __future__ import annotations

from typing import Annotated, Literal

from packaging.requirements import InvalidRequirement, Requirement
from pydantic import BaseModel, ConfigDict, Field, model_validator

from swage.naming import normalize_extra

__all__ = [
    "AddRequirements",
    "Defaults",
    "DynamicPolicy",
    "ExtrasAsOutputs",
    "Family",
    "Feedstock",
    "GitHubUpstream",
    "Output",
    "OutputRun",
    "PyPIUpstream",
    "Quirks",
    "RecipeOwned",
    "RemovalPolicy",
    "RequiresPython",
    "RunConstraint",
    "TrustLevel",
    "Upstream",
]

#: ``manual`` never pushes, ``propose`` pushes but never auto-labels, ``auto``
#: pushes and labels when the trust gates pass (DESIGN.md 5.4).
TrustLevel = Literal["manual", "propose", "auto"]

#: Whether an upstream-dropped removal may merge unattended (DESIGN.md 3.3.8).
#: A proving period, not a permanent rule -- promoted deliberately, in a config
#: commit, once there is a body of reviewed removals swage classified correctly.
RemovalPolicy = Literal["review", "auto"]

#: Whether a dependency list upstream computed at build time may merge
#: unattended (DESIGN.md 3.6.3). `trust` says a PEP 643 `Dynamic: Requires-Dist`
#: is good enough; `review` holds it for a human. The escape hatch if G10 turns
#: out to cost more than it saves.
DynamicPolicy = Literal["review", "trust"]


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


class RequiresPython(_Model):
    """Floor on the Python version swage is willing to build against."""

    min: str


class GitHubUpstream(_Model):
    """Metadata read from a file in a git tag, e.g. the airflow monorepo."""

    source: Literal["github"]
    repo: str
    tag: str
    metadata: str


class PyPIUpstream(_Model):
    """Metadata read from an sdist's ``METADATA`` / ``pyproject.toml``."""

    source: Literal["pypi"]
    project: str | None = None


Upstream = Annotated[GitHubUpstream | PyPIUpstream, Field(discriminator="source")]


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


class OutputRun(_Model):
    """What an existing output's ``run`` section should be built from."""

    core: bool = False
    extras: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _normalized(self) -> OutputRun:
        _check_extras(self.extras, "extras")
        return self


class Output(_Model):
    run: OutputRun


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


class AddRequirements(_Model):
    """conda-forge-only dependencies that upstream never declares.

    Without an entry, a line in the recipe appearing in no upstream version has
    no provenance, fails G1, and stops the feedstock -- deliberately, because
    the alternative is swage deciding on its own whether a maintainer meant it
    (DESIGN.md 3.3.7). With one it is kept for a stated reason.

    Only ``host`` and ``run`` exist, because those are the only sections swage
    plans: ``build`` holds compilers with no relationship to upstream metadata
    (DESIGN.md 3.3.6).
    """

    host: tuple[str, ...] = ()
    run: tuple[str, ...] = ()

    def section(self, name: str) -> tuple[str, ...]:
        return self.run if name == "run" else self.host


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
    requires_python: RequiresPython | None = None
    extras_as_outputs: ExtrasAsOutputs | None = None
    outputs: dict[str, Output] = Field(default_factory=dict)
    name_map: dict[str, str] = Field(default_factory=dict)
    #: Extends the defaults' allowlist rather than replacing it (DESIGN.md 3.3.6).
    recipe_owned: RecipeOwned | None = None
    add_requirements: AddRequirements | None = None
    removals: RemovalPolicy | None = None
    dynamic_dependencies: DynamicPolicy | None = None
    #: conda package name -> what its `run_constraints` entry tracks. An entry
    #: with no association here fails G9 (DESIGN.md 3.3.9).
    run_constraints: dict[str, RunConstraint] = Field(default_factory=dict)
    #: An empty list means "declared, adds nothing", which is materially
    #: different from the key being absent (DESIGN.md 4).
    embedded_extras: dict[str, tuple[str, ...]] = Field(default_factory=dict)

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
    ladder should be stated out loud; new feedstocks start at ``manual``. The
    global name map lives in its own file, not here.
    """

    trust: TrustLevel
    #: Required rather than defaulted, for the same reason `trust` is. The
    #: allowlist is load-bearing: without `python` and `pip` on it, G1 blocks
    #: every feedstock in the fleet, and it should be stated in the file rather
    #: than hidden in code where a config commit cannot reach it.
    recipe_owned: RecipeOwned
    #: Defaulted rather than required, unlike `trust` and `recipe_owned`,
    #: because the safe value is the restrictive one -- a missing policy holds
    #: work for review rather than releasing it.
    removals: RemovalPolicy = "review"
    dynamic_dependencies: DynamicPolicy = "review"
    requires_python: RequiresPython | None = None


class Family(Quirks):
    """``config/families/<name>.yaml``."""

    family: str
    match: FamilyMatch


class Feedstock(Quirks):
    """``config/feedstocks/<name>.yaml``."""

    feedstock: str
    family: str | None = None
