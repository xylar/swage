"""A normalized view of what upstream declares (DESIGN.md 3).

Two decisions here shape everything downstream.

**Source order is preserved.** DESIGN.md 6 requires that requirements coming
from upstream appear in upstream's own order rather than alphabetically, which
keeps swage's diffs against upstream small and legible. That is only possible
if the order survives this layer, so these are tuples and never sets.

**Nothing is collapsed.** A project routinely declares the same dependency
several times under different environment markers::

    pandas>=2.1.2; python_version <"3.13"
    pandas>=2.2.3; python_version >="3.13" and python_version <"3.14"
    pandas>=2.3.3; python_version >="3.14"

conda-forge builds one noarch package, so the recipe ends up with a single
`pandas` line. Choosing which one is a policy decision that belongs to the
planner, where it can be recorded as provenance and gated. This layer reports
what upstream said, all of it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

__all__ = ["UpstreamMetadata", "UpstreamRequirement"]


@dataclass(frozen=True)
class UpstreamRequirement:
    """One dependency exactly as upstream declared it."""

    #: The project name as written upstream, not normalized. Normalization is
    #: the mapping layer's job, and it needs the original to look up quirks.
    name: str
    #: Extras requested *of the dependency*, e.g. ``("boto3",)`` for
    #: ``aiobotocore[boto3]``. These drive `embedded_extras` (DESIGN.md 4).
    extras: tuple[str, ...] = ()
    #: The version specifier as written, e.g. ``">=2.3.3,<3"``. May be empty.
    specifier: str = ""
    #: The PEP 508 environment marker, or None if the requirement is
    #: unconditional.
    marker: str | None = None
    #: The original string, kept so an error message can quote what it saw.
    raw: str = ""

    @property
    def key(self) -> str:
        """Name and extras together, which is what `embedded_extras` is keyed by."""
        if not self.extras:
            return self.name
        return f"{self.name}[{','.join(self.extras)}]"


@dataclass(frozen=True)
class UpstreamMetadata:
    """Everything swage needs from one upstream release."""

    name: str
    version: str | None = None
    requires_python: str | None = None
    #: Upstream's own dependencies, in declaration order.
    dependencies: tuple[UpstreamRequirement, ...] = ()
    #: Extra name -> its dependencies, both in declaration order.
    optional_dependencies: Mapping[str, tuple[UpstreamRequirement, ...]] = field(
        default_factory=dict
    )

    @property
    def extras(self) -> tuple[str, ...]:
        """The extras upstream declares, in declaration order.

        Every one of these has to be accounted for by `supported`, `skip`, or
        `embedded_extras`, or gate G3 stops the feedstock -- which is what
        keeps a newly added upstream extra from silently vanishing.
        """
        return tuple(self.optional_dependencies)
