"""Two kinds of removal, and only one of them is a removal (DESIGN.md 3.3.7).

Adding what upstream declares is routine. "Remove" turns out to be two
different operations wearing the same name, and conflating them is how a tool
like this destroys work.

**Upstream-dropped.** The dependency is in the metadata for the version the
recipe currently reflects and *absent* from the metadata for the version the
bot is bumping to. Upstream made an observable change and the recipe is stale.
This is the exact mirror of an addition -- same evidence, same confidence --
and it is the only case where swage can honestly say the dependency is no
longer needed.

**Never-upstream.** The dependency is in the recipe and in *neither* version's
metadata. Something put it there on the conda-forge side: a runtime import
upstream forgot to declare, a package conda-forge splits differently, a
workaround for something broken elsewhere -- or nothing at all, and it is
drift.

> **swage never removes a never-upstream requirement.** It keeps the line and
> reports it. Keeping preserves a decision that might exist; removing destroys
> one that might. Between two unknowns, only one of them is recoverable.

That case is already covered by G1: a line with no upstream and no config entry
has no `Provenance`, so the feedstock stops until the maintainer writes the
intent down in `add_requirements`. A recipe that has been through swage once is
a recipe whose conda-forge-only dependencies are documented, which is worth
more than the removal would have been.

**Telling them apart costs a second fetch**, of the metadata for the version
the recipe currently reflects. Where that cannot be had -- a yanked release, a
deleted tag -- the removal is *unclassified* and treated as never-upstream:
the safe direction, since the whole point is that swage does not delete on a
guess.
"""

from __future__ import annotations

from collections.abc import Container
from dataclasses import dataclass
from typing import Literal

from swage.config import RecipeOwned
from swage.mapping import normalize_name

from .attribute import AttributionIndex
from .lines import ParsedLine

__all__ = ["Removal", "classify_removal"]

Fate = Literal["kept", "retired", "upstream-dropped", "never-upstream", "unclassified"]


@dataclass(frozen=True)
class Removal:
    """What should happen to a line the current upstream does not ask for.

    ``upstream-dropped`` and ``retired`` are acted on, and even then only when
    policy allows it (G8, DESIGN.md 3.3.8). The rest are kept.
    """

    fate: Fate
    #: The line as the recipe has it.
    text: str
    #: Why, in a sentence the report can print without further assembly.
    reason: str
    #: The version it disappeared in, where that is known. Named in the report
    #: so the maintainer can check the change themselves.
    dropped_in: str | None = None

    @property
    def removed(self) -> bool:
        """Whether swage should actually drop this line."""
        return self.fate in {"upstream-dropped", "retired"}


def classify_removal(
    line: ParsedLine,
    current: AttributionIndex,
    recipe_owned: RecipeOwned,
    previous: AttributionIndex | None = None,
    previous_known: bool = True,
    version: str | None = None,
    retire: Container[str] = frozenset(),
) -> Removal:
    """Decide the fate of one line the current upstream does not declare.

    ``previous`` is an index over the metadata for the version the recipe
    currently reflects. ``previous_known`` distinguishes *"the old metadata was
    fetched and this was not in it"* from *"the old metadata could not be
    fetched at all"* -- an absent index means the second, and an empty one
    could mean either, which is the distinction that decides whether a line is
    safe to drop.
    """
    # Recipe-owned lines are never removals -- they are kept by definition, not
    # by a decision the planner makes (DESIGN.md 3.3.8).
    if line.recipe_owned(recipe_owned):
        return Removal(
            fate="kept",
            text=line.text,
            reason="conda-forge structure, not an upstream dependency",
        )

    if current.contains(line.name):
        return Removal(fate="kept", text=line.text, reason="still declared upstream")

    # Only reachable once upstream has been asked and had nothing to say about
    # this name, in any version and under any extra -- which is what makes
    # `retire` safe to state as a bare name. `google-cloud-storage` declares
    # plain `google-api-core` itself, so its line never arrives here, while
    # the 38 feedstocks whose upstream declares only `google-api-core[grpc]`
    # carry a line that exists for a tool swage is replacing (DESIGN.md 3.2).
    if line.name in retire or normalize_name(line.name) in retire:
        return Removal(
            fate="retired",
            text=line.text,
            reason=(
                f"{line.name!r} is in this feedstock's `retire` list and no "
                "upstream version declares it; removed as the artifact config "
                "says it is"
            ),
        )

    if not previous_known or previous is None:
        return Removal(
            fate="unclassified",
            text=line.text,
            reason=(
                f"{line.name!r} is not in the current metadata, and the "
                "previous version's could not be read to tell whether upstream "
                "dropped it; kept, because swage does not delete on a guess"
            ),
        )

    if previous.contains(line.name):
        where = f" in {version}" if version else ""
        return Removal(
            fate="upstream-dropped",
            text=line.text,
            dropped_in=version,
            reason=f"upstream dropped {line.name!r}{where}",
        )

    return Removal(
        fate="never-upstream",
        text=line.text,
        reason=(
            f"{line.name!r} is in the recipe and in neither version's "
            "metadata; kept, because removing it would undo a maintainer "
            "decision that was never written down"
        ),
    )
