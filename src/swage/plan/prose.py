"""Fencing for the recipe tokens gate details quote.

Every failing gate's detail is published verbatim in the comment swage leaves
on a pull request, and GitHub renders that comment as markdown. A detail that
quotes recipe text is therefore quoting into a markup language, and conda
version specifiers are full of characters that language reads.

The one that bites is `*`. It shipped: the test-matrix detail named
`${{ python_min }}.*` and the `"*"` it added, GitHub paired the two asterisks
into emphasis and consumed both, and the published comment read
`swage added ""` in italics. `would remove 'python 3.10.*'; 'numpy >=1.20.*'`
breaks the same way, and that one is a plain requirement line.

`_` does **not**, which is worth writing down because it looks like it should:
CommonMark forbids intraword emphasis for `_`, so `ruamel_yaml`, `name_map` and
`embedded_extras` all survive unfenced. Checked against GitHub's own renderer
rather than against the spec.

Fencing rather than escaping, because these tokens *are* code and a reader
scanning a comment for the requirement swage is talking about finds it faster
set apart. It also means one rule instead of a list of characters to remember.
"""

from __future__ import annotations

import re

__all__ = ["fenced", "output_phrase", "section_phrase"]

_BACKTICKS = re.compile(r"`+")


def fenced(text: str) -> str:
    """``text`` as a markdown code span, whatever it contains.

    The delimiter grows past the longest backtick run inside, and a token that
    starts or ends with a backtick is padded, which is what the code-span rules
    require. Neither case can arise from a conda requirement -- but this is the
    boundary where upstream metadata becomes published prose, and the defect
    this module exists for was exactly a character nobody expected to matter.
    """
    longest = max((len(run.group()) for run in _BACKTICKS.finditer(text)), default=0)
    ticks = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def section_phrase(section: str, output: str = "") -> str:
    """Where a line is, said the way a recipe's maintainer would say it.

    **Not a path.** The first version of this named the section by its position
    in the parsed document -- `/outputs/1/requirements/run` -- which reads as a
    file somebody is being sent to look for, and numbers the outputs from zero
    besides. Neither is how anyone thinks about a recipe: what identifies that
    block is the package it builds and the section it is in, both of which are
    written in the file in those words.

    ``output`` is the package the section belongs to, which a recipe building
    one package states at the top level and one building several states per
    output. Empty only where the caller has no name to give.
    """
    if output:
        return f"{fenced(output)}'s {fenced(section)} requirements"
    return f"the {fenced(section)} requirements"


def output_phrase(output: str = "", index: int | None = None) -> str:
    """Which package a message is about, said the way the recipe says it.

    `section_phrase`'s rule applied to a message about a whole output rather
    than about one of its sections: **not a path**. `/outputs/1/build` reads
    as a file somebody is being sent to look for and numbers the outputs from
    zero, and neither is how anyone talks about a recipe.

    ``output`` is the package this output builds, or what it stages. ``index``
    is where it sits in `outputs:`, used only when there is no name to give --
    counted from one, because a person reading the file counts from the top.
    Both empty means the recipe builds a single package from its top level and
    there is nothing to distinguish.
    """
    if output:
        return fenced(output)
    if index is not None:
        return f"the recipe's output {index + 1}"
    return "this recipe"
