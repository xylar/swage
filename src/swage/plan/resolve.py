"""Resolve one upstream requirement, extras included (v1 §3.2).

Resolution is keyed on the whole requirement, `google-api-core[grpc]` before
`google-api-core`. An extra the key does not resolve has to be accounted for by
a `name_map` entry keyed on the requirement or by `embedded_extras`; otherwise
the bare name is rendered and the resolution says the extra was dropped, which
G2 stops on.
"""

from __future__ import annotations

from dataclasses import replace

from swage.config import Layered
from swage.mapping import NameResolver, Resolution
from swage.upstream import UpstreamRequirement

__all__ = ["resolve_requirement"]


def resolve_requirement(
    requirement: UpstreamRequirement,
    resolver: NameResolver,
    embedded_extras: Layered[tuple[str, ...]] | None = None,
    mapped: bool = False,
) -> Resolution | None:
    """The conda package this requirement asks for, extras and all.

    ``None`` means nothing could justify an answer, which is G2's business.
    """
    keyed = resolver.resolve(requirement.key, mapped)
    if keyed is not None or not requirement.extras:
        return keyed

    bare = resolver.resolve(requirement.name, mapped)
    if bare is None:
        return None
    written_out = embedded_extras is not None and (
        embedded_extras.lookup(requirement.key) is not None
    )
    if written_out:
        return bare
    return replace(
        bare,
        pypi_name=requirement.key,
        exact=False,
        dropped_extras=requirement.extras,
    )
