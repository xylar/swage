"""Resolve ``python_min`` from the pull request, never from the network.

`python_min` originates as conda-forge's global pinning value and moves
whenever conda-forge drops a Python. Recipes refer to it symbolically as
``${{ python_min }}``, so the number is not in the recipe text -- but swage
still never fetches it, because the value that matters is the one *this*
feedstock builds with, and that is already resolved inside the pull request
swage is reading (DESIGN.md 3.3.3).

Two sources, in order:

1. **the recipe's own ``context.python_min``**, where it sets one. That is what
   ``${{ python_min }}`` expands to in that recipe, so it wins outright.
2. **``.ci_support/*.yaml``** otherwise. conda-smithy renders one per build
   variant with the global pinning and any feedstock-level
   ``conda_build_config.yaml`` already folded in, so it is the resolved answer
   rather than an input to one.

Any single `.ci_support` file will do, because `python_min` cannot differ per
architecture. That is not an assumption: across the 217 feedstock checkouts on
the maintainer's machine that carry it, no feedstock's variants disagree. The
fleet is mid-migration and both live values occur -- 3.9 on 82 checkouts, 3.10
on 135 -- which is exactly why this is read per feedstock rather than assumed.

**This is not `requires_python.min` (DESIGN.md 4), and conflating them is a bug
waiting to happen.** That one is swage's *policy* floor, for refusing a
feedstock whose upstream Python floor rises. This is conda-forge's *build*
floor, and it alone defines the range environment markers are evaluated over.

**Neither source having it is an answer rather than a failure.** `python_min`
is what `${{ python_min }}` expands to and the bottom of the range a single
noarch artifact collapses its markers over -- both questions a `noarch: python`
output asks and an architecture-specific one does not. conda-smithy agrees: it
writes the value into `.ci_support` for a feedstock that builds a noarch python
package and not otherwise, which is why `pyproj` renders 26 variants and
declares it in none of them. So resolving returns None here and the stop
belongs to the output that needed one (DESIGN.md 3.3.3): telling the maintainer
of a compiled feedstock to re-render with conda-smithy is advice that would not
have helped. Where an output does need it, `requires_python.min` is still not a
fallback.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import yaml
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from swage.recipe import Recipe, RecipeOutput

from .errors import PlanError
from .prose import output_phrase

__all__ = [
    "PythonMin",
    "builds_per_python",
    "check_upstream_floor",
    "needs_python_min",
    "python_ceiling",
    "resolve_python_min",
]

#: The key both sources happen to use.
_KEY = "python_min"

#: An upper bound written as a literal, e.g. ``<3.14`` or ``<4.0``. The floor
#: beside it is ``>=${{ python_min }}`` and is deliberately not matched: it is
#: symbolic, and its value is what `resolve_python_min` is for.
_CEILING_CLAUSE = re.compile(r"^<(=?)\s*([0-9][0-9.]*)$")


@dataclass(frozen=True)
class PythonMin:
    """The build floor, and which file said so.

    The source is carried because `swage explain` prints it (DESIGN.md 9.2) and
    because a plan that changed only because conda-forge moved `python_min`
    should be explainable after the fact rather than mysterious.
    """

    #: The version as written, e.g. ``"3.10"``.
    value: str
    #: A file path or a named location, never prose (DESIGN.md 9.2).
    source: str

    @property
    def version(self) -> Version:
        return Version(self.value)


def needs_python_min(recipe: Recipe) -> bool:
    """Whether any output of this recipe has a build floor to state.

    Read by the caller that decides whether to fetch `.ci_support` at all: a
    feedstock whose every output is architecture-specific has no floor to look
    for, so swage does not go looking (DESIGN.md 3.3.3).
    """
    return any(output.noarch == "python" for output in recipe.outputs)


def builds_per_python(recipe: Recipe) -> bool:
    """Whether any output of this recipe is built once per python release.

    The other half of `needs_python_min`, and why `.ci_support` is worth
    fetching for a compiled feedstock too: which pythons it is built for is
    stated nowhere in the recipe, and an upstream declaration gated below the
    oldest of them describes an artifact nobody builds (DESIGN.md 3.3.1.1).
    """
    return any(output.noarch != "python" for output in recipe.outputs)


def resolve_python_min(
    recipe: Recipe, ci_support: Sequence[tuple[str, str]] = ()
) -> PythonMin | None:
    """Resolve the build floor from the recipe, else from ``.ci_support``.

    ``ci_support`` is a sequence of ``(name, text)`` pairs; reading the files
    is the caller's job, so that this stays a pure function of what the pull
    request contains.

    None where neither source declares one, which is what conda-smithy writes
    for a feedstock that builds no noarch python package. An output that needs
    a floor and has none is stopped by the planner, where the message can name
    the output.
    """
    from_recipe = recipe.context.get(_KEY)
    if from_recipe is not None:
        return PythonMin(_checked(from_recipe, f"recipe context.{_KEY}"), "recipe")

    for name, text in ci_support:
        try:
            document = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise PlanError(f"{name}: invalid YAML: {exc}") from exc
        if not isinstance(document, dict) or _KEY not in document:
            continue
        return PythonMin(_checked(_scalar(document[_KEY], name), name), name)

    return None


def check_upstream_floor(
    output: RecipeOutput, requires_python: str | None, python_min: PythonMin
) -> None:
    """Stop where the build floor is a python upstream does not support.

    A `noarch: python` output writes `python ${{ python_min }}.*` in `host` and
    `python >=${{ python_min }}` in `run`. When upstream's own `requires-python`
    floor rises above that value, those lines claim a python upstream no longer
    supports -- and the fix, raising the package's floor, is a packaging
    decision with consequences for everyone downstream (DESIGN.md 4.1).

    **This is the comparison the `requires_python` config key was meant to be**,
    and the reason that key could not be it: the number to compare against is
    this feedstock's own build floor, which is 3.9 on some of the maintainer's
    checkouts and 3.10 on others and moves whenever conda-forge moves it. A
    value in config can only be a third number that agrees with neither.

    Only for an output that builds one noarch package. An architecture-specific
    output has no `python_min` to contradict: which pythons it is built for is
    `.ci_support`'s answer, and `build: skip` is how a recipe narrows it.
    """
    if requires_python is None:
        return
    try:
        supported = SpecifierSet(requires_python)
    except InvalidSpecifier:
        # Upstream metadata is a boundary, and a `requires-python` swage cannot
        # parse says nothing about the floor either way.
        return
    if supported.contains(python_min.value):
        return
    where = output_phrase(output.label, output.index)
    raise PlanError(
        f"python {python_min.value} is not a python upstream supports\n"
        f"    upstream requires-python: {requires_python}\n"
        f"  {where} builds one noarch package for every python from "
        f"{python_min.value} up ({python_min.source}), so the "
        "${{ python_min }} lines in its requirements now claim a python "
        "upstream does not\n"
        "  changing what this package promises is a decision with consequences "
        "for everyone who depends on it, so swage does not make it -- set "
        "context.python_min in the recipe to a python upstream supports"
    )


def python_ceiling(output: RecipeOutput) -> Version | None:
    """The Python this output is *not* built for, where its recipe caps one.

    `python_min` says which Pythons conda-forge builds from; a recipe writing
    ``- python >=${{ python_min }},<3.14`` in `run` says which it stops at, and
    the two together are the range an environment marker has to be evaluated
    over (DESIGN.md 3.3.3). Without the cap, a `python_version >= "3.14"`
    variant is reconciled into a package that will never be installed on 3.14 --
    which on `google-cloud-pubsublite` means demanding a `grpcio` conda-forge
    does not have, the very thing the cap's own comment says the feedstock is
    waiting out.

    Read from the recipe rather than from config because it is a fact about
    this recipe that a human already wrote down, and because it moves the
    moment they lift the cap. Only `run` states it: `host` pins the build
    Python (``python ${{ python_min }}.*``) rather than the range the package
    is installed across.

    A cap swage cannot read as a literal upper bound is no cap at all. That is
    the safe direction -- swage reconciles over the wider range and renders the
    stricter constraint, which a human can see and undo, where the reverse
    would silently drop one.
    """
    block = output.blocks.get("run")
    if block is None:
        return None
    for requirement in block.content.requirements:
        name, _, constraint = requirement.text.partition(" ")
        if name != "python":
            continue
        versions = [
            _exclusive(found)
            for clause in constraint.split(",")
            if (found := _CEILING_CLAUSE.match(clause.strip())) is not None
        ]
        return min(versions) if versions else None
    return None


def _exclusive(bound: re.Match[str]) -> Version:
    """The first Python the bound excludes, so both spellings mean one thing.

    ``<3.14`` already names it. ``<=3.13`` includes 3.13, so the first release
    it excludes is 3.14 -- and treating the two alike would drop a whole Python
    from the range.
    """
    version = Version(bound.group(2))
    if not bound.group(1):
        return version
    return Version(f"{version.major}.{version.minor + 1}")


def _scalar(value: Any, source: str) -> Any:
    """conda-smithy writes a one-element list; a hand-edit may write a scalar."""
    if isinstance(value, list):
        if len(value) != 1:
            raise PlanError(
                f"{source}: {_KEY} has {len(value)} values, expected exactly one"
            )
        return value[0]
    return value


def _checked(value: Any, source: str) -> str:
    # A bare 3.10 in YAML is the float 3.1, which would silently shift the
    # marker range by a whole Python release. Refusing a non-string is the
    # only way to tell that apart from someone meaning 3.1.
    if not isinstance(value, str):
        raise PlanError(
            f"{source}: {_KEY} is {value!r}, not a string -- quote it, since "
            "an unquoted 3.10 is the number 3.1"
        )
    try:
        Version(value)
    except InvalidVersion as exc:
        raise PlanError(f"{source}: {_KEY} is not a version: {value!r}") from exc
    return value
