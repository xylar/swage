"""Packages conda-forge has replaced, written under the new name (DESIGN.md §9.6).

A rule swage is taught, as it is taught the python test matrix: conda-forge's
linter hints that `pkg-config` is deprecated and `pkgconf` is to be used
instead. `pkgconf`'s preferred build installs a `pkg-config` executable, so
the build scripts calling it need nothing else changed.

Made before planning, because the line can sit in `host`, which the planner
writes whole: `shapely` keeps `pkg-config` there. Only a list item or a
`then:`/`else:` value that is the bare name is replaced. A version bound
counts `pkg-config`'s releases, which say nothing about `pkgconf`'s, so a
bounded line is left as it is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from swage.recipe import Recipe

__all__ = ["REPLACED", "Replacement", "replace_packages"]

#: The name conda-forge deprecated -> the package it asks for in its place.
REPLACED = {"pkg-config": "pkgconf"}

#: One requirement on a line of its own: a list item, or a branch's scalar
#: value, then any comment after it.
_ITEM = re.compile(
    r"^(?P<lead>\s*(?:-|then:|else:)\s+)(?P<name>[A-Za-z0-9_.-]+)"
    r"(?P<rest>(?:\s+#.*)?\s*)$"
)


@dataclass(frozen=True)
class Replacement:
    """One deprecated package the recipe named, and what replaced it."""

    was: str
    now: str

    @property
    def summary(self) -> str:
        """One line, for a commit message and the record."""
        return f"{self.was} with {self.now}, which conda-forge asks for instead"


def replace_packages(recipe: Recipe) -> tuple[str, tuple[Replacement, ...]]:
    """``recipe``'s text with every deprecated package renamed, and one
    `Replacement` per name that was, in the order the recipe first names it.
    """
    lines = recipe.text.split("\n")
    seen: set[int] = set()
    replaced: dict[str, Replacement] = {}
    for output in recipe.outputs:
        for block in output.blocks.values():
            for number in range(block.first_line, block.end_line):
                match = _ITEM.match(lines[number])
                if number in seen or match is None or match["name"] not in REPLACED:
                    continue
                seen.add(number)
                was = match["name"]
                now = REPLACED[was]
                lines[number] = f"{match['lead']}{now}{match['rest']}"
                replaced.setdefault(was, Replacement(was, now))
    return "\n".join(lines), tuple(replaced.values())
