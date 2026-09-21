"""Resolve ``python_min`` from the pull request, never from the network (v1
§3.3.3).

Two sources, in order: the recipe's own ``context.python_min``, then any
``.ci_support/*.yaml``, which conda-smithy renders with the pinning folded in.
Neither having it is an answer: a floor is demanded per output, by the one that
needs it (DESIGN.md §9.1). This is conda-forge's build floor, not
`requires_python.min`, which is swage's policy floor (v1 §4).
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

#: An upper bound written as a literal, e.g. ``<3.14``. The symbolic floor
#: beside it is what `resolve_python_min` is for.
_CEILING_CLAUSE = re.compile(r"^<(=?)\s*([0-9][0-9.]*)$")


@dataclass(frozen=True)
class PythonMin:
    """The build floor, and which file said so; `swage explain` prints the
    source.
    """

    #: The version as written, e.g. ``"3.10"``.
    value: str
    #: A file path or a named location, never prose (design-v1.md 9.2).
    source: str

    @property
    def version(self) -> Version:
        return Version(self.value)


def needs_python_min(recipe: Recipe) -> bool:
    """Whether any output of this recipe has a build floor to state, which
    decides whether `.ci_support` is fetched at all (v1 §3.3.3).
    """
    return any(output.noarch == "python" for output in recipe.outputs)


def builds_per_python(recipe: Recipe) -> bool:
    """Whether any output of this recipe is built once per python release, in
    which case `.ci_support` says which (v1 §3.3.1.1).
    """
    return any(output.noarch != "python" for output in recipe.outputs)


def resolve_python_min(
    recipe: Recipe, ci_support: Sequence[tuple[str, str]] = ()
) -> PythonMin | None:
    """Resolve the build floor from the recipe, else from ``.ci_support``.

    ``ci_support`` is a sequence of ``(name, text)`` pairs. None where
    neither declares one; the planner stops an output that needed it.
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
    """Stop where the build floor is a python upstream does not support
    (v1 §4.1).

    Only for an output that builds one noarch package; an arch output has no
    `python_min` to contradict.
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
    """The Python this output is *not* built for, where its recipe caps one
    (v1 §3.3.3).

    Read from `run` only; `host` pins the build Python. A cap swage cannot
    read as a literal upper bound is no cap, which is the safe direction.
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
    """The first Python the bound excludes, so ``<3.14`` and ``<=3.13`` mean
    one thing.
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
    # A bare 3.10 in YAML is the float 3.1; refusing a non-string is the only
    # way to tell that apart from someone meaning 3.1.
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
