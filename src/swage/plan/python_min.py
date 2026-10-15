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
from swage.upstream import RecipeUpstream

from .errors import PlanError
from .prose import fenced, output_phrase

__all__ = [
    "DroppedFloor",
    "FloorChange",
    "PythonMin",
    "RaisedFloor",
    "builds_per_python",
    "conda_forge_python_min",
    "needs_python_min",
    "python_ceiling",
    "reconcile_python_min",
    "resolve_python_min",
]

#: The key both sources happen to use.
_KEY = "python_min"

#: The last minor a raised floor is looked for at; past it, upstream supports
#: no python 3 at all.
_LAST_MINOR = 99

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
    return conda_forge_python_min(ci_support)


def conda_forge_python_min(
    ci_support: Sequence[tuple[str, str]] = (),
) -> PythonMin | None:
    """conda-forge's own floor: what `.ci_support` says, which conda-smithy
    renders from the global pinning whatever the recipe's context sets.
    """
    for name, text in ci_support:
        try:
            document = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise PlanError(f"{name}: invalid YAML: {exc}") from exc
        if not isinstance(document, dict) or _KEY not in document:
            continue
        return PythonMin(_checked(_scalar(document[_KEY], name), name), name)
    return None


@dataclass(frozen=True)
class RaisedFloor:
    """`context.python_min` set to the oldest python upstream supports
    (DESIGN.md §16).
    """

    was: str
    to: str
    #: Upstream's `requires-python`, which asked for it.
    requires_python: str

    @property
    def committed(self) -> str:
        """The commit message's sentence, which has no markdown to fence in."""
        return (
            f"Also raises python_min from {self.was} to {self.to}, the oldest "
            f"Python upstream's requires-python ({self.requires_python}) "
            "allows."
        )

    @property
    def said(self) -> str:
        """Said beside the verdict; the diff shows the edit, this says why."""
        return (
            f"raised `python_min` from {self.was} to {self.to}, the oldest "
            f"python upstream's `requires-python` allows "
            f"({fenced(self.requires_python)})"
        )


@dataclass(frozen=True)
class DroppedFloor:
    """`context.python_min` removed, at or below conda-forge's own floor
    (DESIGN.md §16). A removal, so it is asked about as one.
    """

    was: str
    #: conda-forge's floor, which the package builds from once it is gone.
    conda_forge: str

    @property
    def _why(self) -> str:
        if Version(self.was) == Version(self.conda_forge):
            return f"conda-forge's own floor is also {self.conda_forge}"
        return f"below conda-forge's own floor of {self.conda_forge}"

    @property
    def committed(self) -> str:
        return f"Also removes python_min {self.was} from the context, {self._why}."

    @property
    def said(self) -> str:
        return f"would remove `python_min: {self.was}` from `context`: {self._why}"


#: What changes about the recipe's `python_min`.
FloorChange = RaisedFloor | DroppedFloor


def reconcile_python_min(
    recipe: Recipe,
    upstream: RecipeUpstream,
    python_min: PythonMin,
    conda_forge: PythonMin | None = None,
) -> tuple[PythonMin, FloorChange | None]:
    """The floor the package builds from, and what changes about the recipe's
    `context.python_min` to say it (DESIGN.md §16).

    The entry is needed only where upstream asks for more than conda-forge's
    own floor; one at or below that floor is removed, and one below what
    upstream allows is raised. One above both is a maintainer's and kept.
    The recipe has one `python_min`, so it answers the highest of the noarch
    outputs' floors.
    """
    stated = python_min.source == "recipe"
    covered = (
        stated and conda_forge is not None and python_min.version <= conda_forge.version
    )
    base = conda_forge if covered and conda_forge is not None else python_min
    wanted, requires_python = _wanted_floor(recipe, upstream, base)
    if covered and conda_forge is not None and wanted is None:
        return conda_forge, DroppedFloor(python_min.value, conda_forge.value)
    if wanted is None:
        return python_min, None
    to = f"{wanted.major}.{wanted.minor}"
    return PythonMin(to, "recipe"), RaisedFloor(python_min.value, to, requires_python)


def _wanted_floor(
    recipe: Recipe, upstream: RecipeUpstream, base: PythonMin
) -> tuple[Version | None, str]:
    """The highest of the noarch outputs' upstream floors above ``base``, and
    the `requires-python` that set it; None where ``base`` serves them all.
    An arch output has no `python_min` to contradict.
    """
    highest: tuple[Version, str] | None = None
    for output in recipe.outputs:
        if output.noarch != "python":
            continue
        requires_python = upstream.for_output(output.name or "").requires_python
        floor = _upstream_floor(output, requires_python, base)
        if floor is not None and (highest is None or floor > highest[0]):
            highest = (floor, requires_python or "")
    return (None, "") if highest is None else highest


def _upstream_floor(
    output: RecipeOutput, requires_python: str | None, python_min: PythonMin
) -> Version | None:
    """The oldest minor from ``python_min`` up that upstream supports, or None
    where that is ``python_min`` itself.

    Compared per minor, as the grid evaluates a marker (DESIGN.md §9.2):
    `python_min` names a minor release, so a patch-level floor inside it,
    `gcloud-aio-bigquery`'s ``>=3.11.3`` against 3.11, asks for no raise.
    """
    if requires_python is None:
        return None
    try:
        supported = SpecifierSet(requires_python)
    except InvalidSpecifier:
        # Upstream metadata is a boundary, and a `requires-python` swage cannot
        # parse says nothing about the floor either way.
        return None
    major, minor = python_min.version.release[:2]
    for candidate in range(minor, _LAST_MINOR + 1):
        if supported.contains(f"{major}.{candidate}.99"):
            return None if candidate == minor else Version(f"{major}.{candidate}")
    where = output_phrase(output.label, output.index)
    raise PlanError(
        f"upstream supports no python from {python_min.value} up\n"
        f"    upstream requires-python: {requires_python}\n"
        f"  {where} builds one noarch package for every python from "
        f"{python_min.value} up ({python_min.source}), and no python_min "
        "would make that a python upstream supports"
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
