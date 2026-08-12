"""Pydantic schema for the quirks database (DESIGN.md 4).

Every layer of the database is validated against these models with
``extra="forbid"``, so a mistyped key is a startup error rather than a silently
ignored setting.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "Defaults",
    "ExtrasAsOutputs",
    "Family",
    "Feedstock",
    "GitHubUpstream",
    "Output",
    "OutputRun",
    "PyPIUpstream",
    "Quirks",
    "RequiresPython",
    "TrustLevel",
    "Upstream",
]

#: ``manual`` never pushes, ``propose`` pushes but never auto-labels, ``auto``
#: pushes and labels when the trust gates pass (DESIGN.md 5.4).
TrustLevel = Literal["manual", "propose", "auto"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


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


class Output(_Model):
    run: OutputRun


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
    #: An empty list means "declared, adds nothing", which is materially
    #: different from the key being absent (DESIGN.md 4).
    embedded_extras: dict[str, tuple[str, ...]] = Field(default_factory=dict)


class Defaults(_Model):
    """``config/defaults.yaml`` -- global policy.

    ``trust`` is required rather than defaulted because the bottom of the trust
    ladder should be stated out loud; new feedstocks start at ``manual``. The
    global name map lives in its own file, not here.
    """

    trust: TrustLevel
    requires_python: RequiresPython | None = None


class Family(Quirks):
    """``config/families/<name>.yaml``."""

    family: str
    match: FamilyMatch


class Feedstock(Quirks):
    """``config/feedstocks/<name>.yaml``."""

    feedstock: str
    family: str | None = None
