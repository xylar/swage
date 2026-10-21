"""Raise the python floor an architecture-specific output's `build.skip`
states, where upstream's `requires-python` rose above it (DESIGN.md §16).

An arch output is built once per python `.ci_support` renders, so a python
upstream refuses is a build that fails. Its floor is a `match(python, "<3.12")`
condition in `build.skip`, where a noarch output's is `context.python_min`
(`python_min.py`), and it is raised for the same reason: the answer is
upstream's, and there is only one.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from packaging.specifiers import InvalidSpecifier, SpecifierSet

from swage.recipe import Recipe, RecipeOutput
from swage.upstream import RecipeUpstream

from .errors import PlanError
from .prose import fenced, output_phrase
from .python_min import spans_pythons

__all__ = ["RaisedSkip", "UnskippedPythons", "reconcile_skip_floors"]

#: A condition that skips every python below one minor, the way the fleet's
#: recipes write it: `match(python, "<3.12")`, or `<=3.11` for the same thing.
_FLOOR = re.compile(r"""^match\(\s*python\s*,\s*(['"])(<=?)\s*3\.([0-9]+)\1\s*\)$""")


@dataclass(frozen=True)
class RaisedSkip:
    """A `build.skip` condition rewritten to skip every python upstream's
    `requires-python` excludes (DESIGN.md §16).
    """

    #: Where the output sits in `recipe.outputs`, whose pythons start at `to`.
    position: int
    #: Which package, said the way the recipe says it; empty for a recipe
    #: building one package, where naming it says nothing.
    output: str
    path: str
    #: The condition as it will read.
    text: str
    was: str
    to: str
    #: Upstream's `requires-python`, which asked for it.
    requires_python: str

    @property
    def minor(self) -> int:
        return int(self.to.split(".")[1])

    @property
    def committed(self) -> str:
        """The commit message's sentence, which has no markdown to fence in."""
        owner = f" of {self.output.strip('`')}" if self.output else ""
        return (
            f"Also raises the Python floor in build.skip{owner} from {self.was} "
            f"to {self.to}, the oldest Python upstream's requires-python "
            f"({self.requires_python}) allows."
        )

    @property
    def said(self) -> str:
        """Said beside the verdict; the diff shows the edit, this says why."""
        owner = f" of {self.output}" if self.output else ""
        return (
            f"raised the python floor in `build.skip`{owner} from {self.was} to "
            f"{self.to}, the oldest python upstream's `requires-python` allows "
            f"({fenced(self.requires_python)})"
        )


@dataclass(frozen=True)
class UnskippedPythons:
    """Pythons an arch output is built for and upstream excludes, where its
    `build.skip` has no floor to raise (DESIGN.md §16).
    """

    output: str
    pythons: tuple[int, ...]
    #: The oldest python upstream allows, which the skip would start from.
    to: str
    requires_python: str

    @property
    def said(self) -> str:
        built = ", ".join(f"3.{minor}" for minor in self.pythons)
        return (
            f"{self.output} is built for python {built}, which upstream's "
            f"`requires-python` excludes ({fenced(self.requires_python)}), and "
            "`build.skip` states no python floor to raise"
        )

    @property
    def remedy(self) -> str:
        return f'skip it in the recipe\'s build.skip: match(python, "<{self.to}")'


def reconcile_skip_floors(
    recipe: Recipe, upstream: RecipeUpstream, pythons: Sequence[int]
) -> tuple[tuple[RaisedSkip, ...], tuple[UnskippedPythons, ...]]:
    """Every arch output's `build.skip` raised to the oldest python upstream
    allows, and the outputs whose skip states no floor to raise.

    ``pythons`` is the minors `.ci_support` renders. A condition already at
    or above upstream's floor is a recipe a re-render has not caught up with,
    and needs neither.
    """
    built = tuple(sorted(set(pythons)))
    if not built:
        return (), ()
    raised: list[RaisedSkip] = []
    unskipped: list[UnskippedPythons] = []
    single = len(recipe.outputs) == 1
    for position, output in enumerate(recipe.outputs):
        # One artifact for every python from the floor up has `python_min`
        # for its floor, raised in `python_min.py` (DESIGN.md §1).
        if spans_pythons(output) or not _takes_python(output):
            continue
        requires_python = upstream.for_output(output.name or "").requires_python
        wanted = _upstream_floor(output, requires_python, built)
        if wanted is None or requires_python is None:
            continue
        floors = {condition.path: _floors(condition.text) for condition in output.skip}
        if any(
            _floor_of(match) >= wanted
            for found in floors.values()
            for _, match in found
        ):
            continue
        rewritable = [
            condition
            for condition in output.skip
            if floors[condition.path] and condition.line is not None
        ]
        where = "" if single else output_phrase(output.label, output.index)
        to = f"3.{wanted}"
        if not rewritable:
            unskipped.append(
                UnskippedPythons(
                    where or output_phrase(output.label, output.index),
                    tuple(minor for minor in built if minor < wanted),
                    to,
                    requires_python,
                )
            )
            continue
        for condition in rewritable:
            found = floors[condition.path]
            raised.append(
                RaisedSkip(
                    position,
                    where,
                    condition.path,
                    _rewritten(condition.text, found, wanted),
                    f"3.{min(_floor_of(match) for _, match in found)}",
                    to,
                    requires_python,
                )
            )
    return tuple(raised), tuple(unskipped)


def _takes_python(output: RecipeOutput) -> bool:
    """Whether the output is built against python at all; a C library in a
    feedstock that also builds bindings is not, whatever `.ci_support` lists.
    """
    return any(
        text.partition(" ")[0] == "python"
        for name in ("host", "run")
        if (block := output.blocks.get(name)) is not None
        for text in block.content.texts()
    )


def _upstream_floor(
    output: RecipeOutput, requires_python: str | None, built: tuple[int, ...]
) -> int | None:
    """The oldest minor the output is built for that upstream supports, where
    that is above the oldest it is built for; None where nothing needs to move.

    Compared per minor, as `python_min.py` compares a noarch floor: upstream
    supports a minor when it supports its last patch.
    """
    if requires_python is None:
        return None
    try:
        supported = SpecifierSet(requires_python)
    except InvalidSpecifier:
        return None
    for minor in range(built[0], built[-1] + 1):
        if supported.contains(f"3.{minor}.99"):
            return None if minor == built[0] else minor
    where = output_phrase(output.label, output.index)
    raise PlanError(
        f"upstream supports no python {where} is built for\n"
        f"    upstream requires-python: {requires_python}\n"
        f"  .ci_support builds it for python "
        f"{', '.join(f'3.{minor}' for minor in built)}, and no skip would leave "
        "a python upstream supports"
    )


def _floors(text: str) -> list[tuple[int, re.Match[str]]]:
    """The python floors a skip condition states, each with where it starts.

    Only a whole condition, or one alternative of an `or` at its top level:
    skipping more pythons there skips more and nothing else, which is not
    true under an `and` or a `not`.
    """
    found = []
    for start, end in _alternatives(text):
        match = _FLOOR.match(text[start:end])
        if match is not None:
            found.append((start, match))
    return found


def _alternatives(text: str) -> list[tuple[int, int]]:
    """The spans `or` separates at the top level of ``text``, stripped. None
    at all where ``text`` is a conditional expression, whose `or` is inside it.
    """
    spans: list[tuple[int, int]] = []
    depth, quote, start, index = 0, "", 0, 0
    while index < len(text):
        char = text[index]
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif depth == 0 and text.startswith(" if ", index):
            return []
        elif depth == 0 and text.startswith(" or ", index):
            spans.append((start, index))
            start = index = index + len(" or ")
            continue
        index += 1
    spans.append((start, len(text)))
    stripped = []
    for begin, end in spans:
        span = text[begin:end]
        stripped.append(
            (begin + len(span) - len(span.lstrip()), begin + len(span.rstrip()))
        )
    return stripped


def _floor_of(match: re.Match[str]) -> int:
    """The oldest minor the condition leaves built."""
    minor = int(match.group(3))
    return minor if match.group(2) == "<" else minor + 1


def _rewritten(
    text: str, floors: Sequence[tuple[int, re.Match[str]]], wanted: int
) -> str:
    """``text`` with every floor in it raised to ``wanted``, each in the
    operator the recipe chose. From the right, so the offsets to its left
    stay valid.
    """
    for start, match in sorted(floors, key=lambda found: found[0], reverse=True):
        minor = wanted if match.group(2) == "<" else wanted - 1
        text = f"{text[: start + match.start(3)]}{minor}{text[start + match.end(3) :]}"
    return text
