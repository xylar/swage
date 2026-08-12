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

**Extra names are the one exception, and they must be normalized.** The two
metadata sources disagree about how an extra is spelled, for the same release
of the same project. `google-cloud-bigquery` 3.43.0's sdist carries both files:

    pyproject.toml   [project.optional-dependencies]  bigquery_v2
    PKG-INFO         Provides-Extra:                  bigquery-v2

Build backends apply PEP 685 when they write METADATA, and nothing applies it
to `pyproject.toml`. The airflow providers make this routine rather than
exotic -- `apache.iceberg`, `cncf.kubernetes`, `microsoft.azure`, `GSSAPI` all
come back hyphenated and lowercased through METADATA -- and it reaches
dependency extras too, where `pyhive[hive_pure_sasl]` becomes
`pyhive[hive-pure-sasl]`.

Left alone, an extra's name would depend on which file an sdist happened to
ship. Config lookups keyed on the other spelling would miss silently, G3 would
report an extra the maintainer had already accounted for, and the marker
comments swage renders would change spelling for no reason the diff explains.
So every extra name is PEP 685-normalized on the way in, from both sources,
and config is written in that form. Package names are *not* normalized here:
that is the mapping layer's job (DESIGN.md 3.2), and it needs the original.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

__all__ = ["UpstreamMetadata", "UpstreamRequirement", "normalize_extra"]

_SEPARATORS = re.compile(r"[-_.]+")


def normalize_extra(name: str) -> str:
    """PEP 685 normalization: lowercase, and runs of ``-_.`` become ``-``.

    Deliberately duplicates the rule `mapping.normalize_name` applies to
    package names rather than importing it -- the mapping layer sits above
    this one, and PEP 685 and PEP 503 are free to diverge even though PEP 685
    defers to PEP 503's algorithm today.
    """
    return _SEPARATORS.sub("-", name).lower()


@dataclass(frozen=True)
class UpstreamRequirement:
    """One dependency exactly as upstream declared it."""

    #: The project name as written upstream, not normalized. Normalization is
    #: the mapping layer's job, and it needs the original to look up quirks.
    name: str
    #: Extras requested *of the dependency*, e.g. ``("boto3",)`` for
    #: ``aiobotocore[boto3]``. PEP 685-normalized and sorted, so that the
    #: `key` below is the same string whichever metadata source it came
    #: from. These drive `embedded_extras` (DESIGN.md 4).
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
    #: What upstream needs to *build*, in declaration order -- PEP 518's
    #: ``[build-system] requires``. This is where a recipe's `host` section
    #: comes from, exact pin included: `flit-core ==3.12.0` is upstream's
    #: `flit_core==3.12.0` and not a conda-forge convention (DESIGN.md 3.3.6).
    #:
    #: `None` means upstream declared no `[build-system]` table at all, which
    #: is not the same as declaring an empty one. Absent means the build
    #: backend is PEP 517's implicit setuptools fallback and swage was told
    #: nothing; empty means upstream said "nothing to build with". Collapsing
    #: the two would let the planner read silence as "host should be emptied".
    build_requires: tuple[UpstreamRequirement, ...] | None = None
    #: Upstream's own dependencies, in declaration order.
    dependencies: tuple[UpstreamRequirement, ...] = ()
    #: Extra name -> its dependencies, both in declaration order. Keys are
    #: PEP 685-normalized, so they match whichever source they were read from.
    optional_dependencies: Mapping[str, tuple[UpstreamRequirement, ...]] = field(
        default_factory=dict
    )

    @property
    def extras(self) -> tuple[str, ...]:
        """The extras upstream declares, normalized, in declaration order.

        Every one of these has to be accounted for by `supported`, `skip`, or
        `embedded_extras`, or gate G3 stops the feedstock -- which is what
        keeps a newly added upstream extra from silently vanishing.
        """
        return tuple(self.optional_dependencies)
