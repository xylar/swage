"""Fencing for the recipe tokens findings quote.

A finding is published as markdown, and conda specifiers are full of `*`, which
GitHub reads as emphasis. Fencing rather than escaping: the tokens are code, and
one rule beats a list of characters.
"""

from __future__ import annotations

import re

__all__ = ["fenced", "output_phrase", "section_phrase"]

_BACKTICKS = re.compile(r"`+")


def fenced(text: str) -> str:
    """``text`` as a markdown code span, whatever it contains.

    The delimiter grows past the longest backtick run inside, and a token that
    starts or ends with a backtick is padded.
    """
    longest = max((len(run.group()) for run in _BACKTICKS.finditer(text)), default=0)
    ticks = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def section_phrase(section: str, output: str = "") -> str:
    """Where a line is, said the way a recipe's maintainer would say it: the
    package and the section, never a path (DESIGN.md §9.7).

    ``output`` is the package the section belongs to; empty only where the
    caller has no name to give.
    """
    if output:
        return f"{fenced(output)}'s {fenced(section)} requirements"
    return f"the {fenced(section)} requirements"


def output_phrase(output: str = "", index: int | None = None) -> str:
    """Which package a message is about, said the way the recipe says it.

    ``output`` is the package this output builds, or what it stages. ``index``
    is where it sits in `outputs:`, counted from one, used only when there is
    no name. Both empty means a single package built from the top level.
    """
    if output:
        return fenced(output)
    if index is not None:
        return f"the recipe's output {index + 1}"
    return "this recipe"
