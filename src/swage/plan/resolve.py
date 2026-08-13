"""Resolve one upstream requirement, extras included (DESIGN.md 3.2).

conda-forge frequently publishes a dependency's extra under a *name of its
own*, so resolution is keyed on the whole requirement -- `google-api-core[grpc]`
before `google-api-core`. `google-api-core-grpc` is the second output of the
`google-api-core` split recipe: `pin_subpackage(google-api-core, exact=true)`
plus `grpcio` and `grpcio-status`, so it is the base package *with* the extra's
dependencies rather than a separate build of anything. Only its name reaches
it. Where the key resolves, that is the answer and there is nothing else to
decide.

**The interesting case is where it does not.** Falling back to the bare name
produces a conda name that builds and under-specifies: `celery[redis]` becomes
`celery`, the recipe never mentions `redis-py`, and nothing is visibly wrong
until something fails to import at runtime. That is the failure this project
exists to prevent, and it is a *guess* -- swage would be asserting that the
extra means nothing on conda-forge, which is a claim no metadata it has read
supports.

So the extra has to be accounted for, and there are exactly two ways:

1. **a `name_map` entry keyed on the requirement.** `google-api-core[grpc]` ->
   `google-api-core-grpc` where conda-forge splits the package, and
   `google-auth[pyopenssl]` -> `google-auth` where the extra needs nothing
   conda-forge does not already ship. The second is an identity entry and it is
   load-bearing, not redundant -- it is how "considered, and the bare name is
   right" gets on the record.
2. **an `embedded_extras` entry**, which writes out what the extra pulls in
   (DESIGN.md 4) and leaves the bare name correct as far as it goes.

With neither, the bare name is still what swage renders -- it never mangles a
line it cannot justify -- but the resolution says the extra was dropped, and
G2 stops the feedstock rather than merging an under-specified recipe.
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
) -> Resolution | None:
    """The conda package this requirement asks for, extras and all.

    ``None`` means nothing could justify an answer, which is G2's business
    rather than this function's -- the same result `NameResolver.resolve`
    returns for a name conda-forge does not publish.
    """
    keyed = resolver.resolve(requirement.key)
    if keyed is not None or not requirement.extras:
        return keyed

    bare = resolver.resolve(requirement.name)
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
