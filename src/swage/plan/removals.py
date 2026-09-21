"""Three kinds of removal, and only two of them are removals (DESIGN.md §9.5).

`upstream-dropped` and `out-of-range` rest on metadata swage read;
`never-upstream` is kept and reported, because G1 already holds the line until
config explains it (v1 §3.3.7). Telling them apart costs a second fetch; where
that cannot be had the removal is `unclassified` and kept.
"""

from __future__ import annotations

from collections.abc import Container, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from swage.config import RecipeOwned
from swage.mapping import normalize_name

from .attribute import AttributionIndex
from .lines import ParsedLine

__all__ = ["Removal", "classify_removal"]

Fate = Literal[
    "kept",
    "retired",
    "upstream-dropped",
    "out-of-range",
    "never-upstream",
    "unclassified",
]


@dataclass(frozen=True)
class Removal:
    """What should happen to a line the current upstream does not ask for.

    ``upstream-dropped``, ``out-of-range`` and ``retired`` are acted on, under
    G8's policy (DESIGN.md §9.5). The rest are kept.
    """

    fate: Fate
    #: The line as the recipe has it.
    text: str
    #: Why, in a sentence the report can print without further assembly.
    reason: str
    #: The version it disappeared in, where that is known. Named in the report
    #: so the maintainer can check the change themselves.
    dropped_in: str | None = None
    #: The pythons upstream gates every declaration of it on, for an
    #: ``out-of-range`` removal: the one fact a reader checks.
    declared_for: str | None = None

    @property
    def removed(self) -> bool:
        """Whether swage should actually drop this line."""
        return self.fate in {"upstream-dropped", "out-of-range", "retired"}


def classify_removal(
    line: ParsedLine,
    current: AttributionIndex,
    recipe_owned: RecipeOwned,
    previous: AttributionIndex | None = None,
    previous_known: bool = True,
    version: str | None = None,
    retire: Container[str] = frozenset(),
    out_of_range: Mapping[str, str] = MappingProxyType({}),
    built_for: str = "",
) -> Removal:
    """Decide the fate of one line the current upstream does not declare.

    ``previous`` is an index over the metadata for the version the recipe
    currently reflects; ``previous_known`` says whether it was fetched at
    all. ``out_of_range`` and ``built_for`` are what the planner worked out
    while collapsing markers.
    """
    # Recipe-owned lines are never removals -- they are kept by definition, not
    # by a decision the planner makes (design-v1.md 3.3.8).
    if line.recipe_owned(recipe_owned):
        return Removal(
            fate="kept",
            text=line.text,
            reason="conda-forge structure, not an upstream dependency",
        )

    # Before `contains`, which would answer yes: upstream declares this package,
    # for pythons this output is not built for.
    declared_for = _lookup(out_of_range, line.name)
    if declared_for is not None:
        return Removal(
            fate="out-of-range",
            text=line.text,
            declared_for=declared_for,
            reason=(
                f"upstream declares {line.name!r} only for {declared_for}, and "
                f"this recipe is built for {built_for}, so no package it "
                "builds installs it"
            ),
        )

    if current.contains(line.name):
        return Removal(fate="kept", text=line.text, reason="still declared upstream")

    # Reachable only once upstream has been asked and had nothing to say about
    # this name, in any version and under any extra (v1 §3.2).
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


def _lookup(out_of_range: Mapping[str, str], name: str) -> str | None:
    """What the planner recorded for ``name``, under either spelling of it,
    matched as `AttributionIndex.contains` matches.
    """
    for key in (name, normalize_name(name)):
        found = out_of_range.get(key)
        if found is not None:
            return found
    return None
