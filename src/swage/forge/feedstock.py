"""Read a feedstock's files at one commit (v1 §3.5; DESIGN.md §10).

Everything comes out of the pull request's own head, through the contents API.
v0 is routed by filename, not parsed (v1 §3.1). `.ci_support` is a separate read
because most recipes need it (v1 §3.3.3).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import yaml

from .errors import ForgeError, NotFound
from .github import GitHub

__all__ = [
    "CiSupport",
    "FeedstockFiles",
    "Repository",
    "read_ci_support",
    "read_feedstock",
    "repository",
]

RECIPE_V1 = "recipe/recipe.yaml"
RECIPE_V0 = "recipe/meta.yaml"
CI_SUPPORT = ".ci_support"


@dataclass(frozen=True)
class FeedstockFiles:
    """What one feedstock's pull request contains, as text."""

    feedstock: str
    ref: str
    #: None where the feedstock is still v0, which is a routing decision
    #: rather than a failure.
    recipe: str | None = None
    #: True where `recipe/meta.yaml` was found instead. The feedstock is
    #: reported as NEEDS MIGRATION and otherwise untouched.
    v0: bool = False

    @property
    def repo(self) -> str:
        return f"conda-forge/{self.feedstock}-feedstock"


def read_feedstock(github: GitHub, feedstock: str, ref: str) -> FeedstockFiles:
    """Read the recipe at ``ref``, or route the feedstock to migration. The build
    floor is `read_ci_support`, separately.
    """
    repo = f"conda-forge/{feedstock}-feedstock"
    try:
        recipe = github.file(repo, RECIPE_V1, ref)
    except NotFound:
        # The filename is the routing decision, checked before anything is
        # parsed (v1 §3.1).
        try:
            github.file(repo, RECIPE_V0, ref)
        except NotFound as exc:
            raise ForgeError(
                f"{feedstock}: has neither {RECIPE_V1} nor {RECIPE_V0} at {ref}"
            ) from exc
        return FeedstockFiles(feedstock=feedstock, ref=ref, v0=True)

    return FeedstockFiles(feedstock=feedstock, ref=ref, recipe=recipe)


#: The python a rendered variant is built for, as conda-smithy names the file,
#: free-threaded builds included.
_VARIANT_PYTHON = re.compile(r"python(\d+)\.(\d+)")

#: The platform a rendered variant is built for, the first token of the file
#: name, in a recipe selector's vocabulary.
_VARIANT_PLATFORM = re.compile(r"^(linux|osx|win)_")


@dataclass(frozen=True)
class CiSupport:
    """What `.ci_support` says about how a feedstock is built
    (docs/conda-forge.md).
    """

    #: ``(name, text)`` pairs, the shape `resolve_python_min` takes. One file,
    #: since `python_min` cannot differ per variant (design-v1.md 3.3.3).
    files: tuple[tuple[str, str], ...] = ()
    #: The minor releases of python 3 this feedstock is built for. Empty where
    #: it builds no python variants, or was never rendered.
    pythons: tuple[int, ...] = ()
    #: The platforms this feedstock is built for. More than one on a noarch
    #: output is `noarch_platforms` (DESIGN.md §9.1).
    platforms: tuple[str, ...] = ()
    #: The variant keys the rendered config carries, with `_` as `-`, so they
    #: read as package names: a `host` line naming one takes no bound (DESIGN.md
    #: §9.4). Every key, because the set is only ever asked about a package.
    pinned: frozenset[str] = frozenset()


def read_ci_support(github: GitHub, feedstock: str, ref: str) -> CiSupport:
    """The rendered build configs, as far as anything needs them: the listing
    says which pythons and platforms are built, and the first file answers
    `python_min` (v1 §3.3.3).
    """
    repo = f"conda-forge/{feedstock}-feedstock"
    try:
        listing = github.api(f"repos/{repo}/contents/{CI_SUPPORT}", {"ref": ref})
    except NotFound:
        # conda-smithy has never rendered this feedstock. The planner stops
        # rather than assuming a floor, and says so.
        return CiSupport()
    if not isinstance(listing, Sequence):
        raise ForgeError(f"{repo}: {CI_SUPPORT} is not a directory")
    names = sorted(
        str(entry["name"])
        for entry in listing
        if isinstance(entry, Mapping)
        and entry.get("type") == "file"
        and str(entry.get("name", "")).endswith(".yaml")
    )
    if not names:
        return CiSupport()
    text = github.file(repo, f"{CI_SUPPORT}/{names[0]}", ref)
    return CiSupport(
        files=((names[0], text),),
        pythons=_variant_pythons(names),
        platforms=_variant_platforms(names),
        pinned=_variant_pins(text),
    )


def _variant_pins(text: str) -> frozenset[str]:
    """The packages conda-forge's global pinning supplies a version for. One
    file answers for all of them: the variants differ in values, not keys.
    """
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        # conda-smithy wrote it, so this does not happen; an unreadable file
        # means swage bounds a line the pinning would have.
        return frozenset()
    if not isinstance(document, Mapping):
        return frozenset()
    return frozenset(str(key).replace("_", "-") for key in document)


def _variant_pythons(names: Sequence[str]) -> tuple[int, ...]:
    """The python 3 minor releases named by a set of variant file names."""
    found = {
        int(match.group(2))
        for name in names
        if (match := _VARIANT_PYTHON.search(name)) and match.group(1) == "3"
    }
    return tuple(sorted(found))


def _variant_platforms(names: Sequence[str]) -> tuple[str, ...]:
    """The platforms named by a set of variant file names, in recipe order."""
    found = {
        match.group(1) for name in names if (match := _VARIANT_PLATFORM.match(name))
    }
    return tuple(platform for platform in ("linux", "osx", "win") if platform in found)


@dataclass(frozen=True)
class Repository:
    """What one call to `repos/{owner}/{repo}` says a feedstock is: both facts
    together, so a caller cannot get a ref to read at without being told
    whether the feedstock accepts writes.
    """

    feedstock: str
    #: Which ref to read at. Asked rather than assumed: a feedstock still on
    #: `master` read at `main` comes back as having no recipe.
    default_branch: str
    #: Archived on GitHub: read-only, and nothing can be pushed to it, merged
    #: into it or labeled on it ever again.
    archived: bool = False

    @property
    def repo(self) -> str:
        return f"conda-forge/{self.feedstock}-feedstock"


def repository(github: GitHub, feedstock: str) -> Repository:
    """Ask GitHub what this feedstock is (v1 §8.2), for the one command with no
    pull request to be handed a ref by.
    """
    repo = f"conda-forge/{feedstock}-feedstock"
    payload = github.api(f"repos/{repo}")
    if not isinstance(payload, Mapping):
        raise ForgeError(f"{repo}: repository metadata was not an object")
    branch = payload.get("default_branch")
    if not isinstance(branch, str) or not branch:
        raise ForgeError(f"{repo}: has no default branch")
    return Repository(
        feedstock=feedstock,
        default_branch=branch,
        archived=payload.get("archived") is True,
    )
