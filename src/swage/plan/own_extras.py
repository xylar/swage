"""Expand an extra that names the project's own other extras (v1 §3.3.12).

`all = ["pkg[a,b]"]` read literally is one dependency, the package being built.
What it installs is `a`'s and `b`'s dependencies, so that is what the planner
reconciles against. It happens here rather than in the reader because config
can overrule it: a `name_map` entry for `pkg[a]`, as `wetterdienst[restapi]`
has, says the recipe depends on the output that publishes `a` instead.
"""

from __future__ import annotations

from dataclasses import replace

from packaging.utils import canonicalize_name

from swage.mapping import NameResolver
from swage.upstream import UpstreamMetadata, UpstreamRequirement

__all__ = ["expand_own_extras"]


def expand_own_extras(
    release: UpstreamMetadata, resolver: NameResolver
) -> UpstreamMetadata:
    """``release`` with each reference to its own extras spliced in.

    Detection is structural: the requirement's name is the project's, whatever
    the extra is called. Splicing is recursive and in place, so source order
    survives, and a cycle stops at the first extra it revisits. SQLAlchemy's
    legacy spellings are such a cycle: `mssql_pymssql` names
    `sqlalchemy[mssql-pymssql]`, which is itself once normalized. A marker on
    the reference is ANDed onto what it brings in.
    """
    own = canonicalize_name(release.name)
    optional = release.optional_dependencies

    def expand(
        extra: str, marker: str | None, visited: frozenset[str]
    ) -> list[UpstreamRequirement]:
        found: list[UpstreamRequirement] = []
        for requirement in optional.get(extra, ()):
            combined = _conjoin(marker, requirement.marker)
            if (
                canonicalize_name(requirement.name) != own
                or resolver.resolve(requirement.key, release.conda_names) is not None
            ):
                found.append(_with_marker(requirement, combined))
                continue
            for named in requirement.extras:
                if named not in visited:
                    found.extend(expand(named, combined, visited | {named}))
        return found

    return replace(
        release,
        optional_dependencies={
            extra: _unique(expand(extra, None, frozenset({extra})))
            for extra in optional
        },
    )


def _conjoin(outer: str | None, inner: str | None) -> str | None:
    if outer is None or inner is None:
        return outer or inner
    return f"({outer}) and ({inner})"


def _with_marker(
    requirement: UpstreamRequirement, marker: str | None
) -> UpstreamRequirement:
    if marker == requirement.marker:
        return requirement
    return replace(requirement, marker=marker)


def _unique(
    requirements: list[UpstreamRequirement],
) -> tuple[UpstreamRequirement, ...]:
    """First occurrence of each requirement, so two spliced extras sharing a
    dependency bring it once.
    """
    seen: dict[tuple[str, tuple[str, ...], str, str | None], UpstreamRequirement] = {}
    for requirement in requirements:
        key = (
            requirement.name,
            requirement.extras,
            requirement.specifier,
            requirement.marker,
        )
        seen.setdefault(key, requirement)
    return tuple(seen.values())
