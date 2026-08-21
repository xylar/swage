"""Read a feedstock's files at one commit (DESIGN.md 3.5).

Everything the planner needs about a feedstock comes out of the pull request
being read, at the pull request's own head: the recipe, and the build floor
that `${{ python_min }}` expands to. None
of it is fetched from anywhere else and none of it needs a clone -- at several
hundred feedstocks, cloning to read is untenable, and only a feedstock that
actually needs a commit is ever cloned.

**v0 is routed, not parsed.** Most of the fleet still carries `meta.yaml`, and
a v0 recipe does not parse as YAML at all -- `{{ name }}` at the start of a
value opens a flow mapping. Surfacing that as "invalid YAML" would make the
single most common condition in the fleet look like a corrupt file, so the
filename is checked first and the feedstock is reported as needing migration
(DESIGN.md 3.1).

**`.ci_support` is a separate read, because most recipes need it.** Only 4 of
the 60 noarch feedstocks in the maintainer's checkouts set their own
`context.python_min`; 55 refer to `${{ python_min }}` without setting it, so
the build floor comes from a rendered `.ci_support` file (DESIGN.md 3.3.3).
That makes it the common path rather than the exception -- which is why it is
its own function rather than a flag on this one. A flag would mean the caller
discovering it needs the floor *after* the recipe has been read, and reading
the recipe a second time to get it.
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
    "default_branch",
    "read_ci_support",
    "read_feedstock",
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
    """Read the recipe at ``ref``, or route the feedstock to migration.

    The build floor is `read_ci_support`, separately, because a caller only
    knows whether it needs one after reading the recipe -- and almost always
    does.
    """
    repo = f"conda-forge/{feedstock}-feedstock"
    try:
        recipe = github.file(repo, RECIPE_V1, ref)
    except NotFound:
        # The filename is the routing decision, checked before anything is
        # parsed, so the fleet's most common condition does not surface as a
        # corrupt file (DESIGN.md 3.1).
        try:
            github.file(repo, RECIPE_V0, ref)
        except NotFound as exc:
            raise ForgeError(
                f"{feedstock}: has neither {RECIPE_V1} nor {RECIPE_V0} at {ref}"
            ) from exc
        return FeedstockFiles(feedstock=feedstock, ref=ref, v0=True)

    return FeedstockFiles(feedstock=feedstock, ref=ref, recipe=recipe)


#: The python a rendered variant is built for, as conda-smithy names the file:
#: `linux_aarch64_python3.12.____cpython.yaml`, and `python3.14.____cp314t` for
#: the free-threaded build of the same release.
_VARIANT_PYTHON = re.compile(r"python(\d+)\.(\d+)")

#: The platform a rendered variant is built for, which conda-smithy writes as
#: the first token of the file name: `linux_64_.yaml`, `osx_arm64_....yaml`.
#: The vocabulary is the one a recipe selector uses, so it needs no translating
#: on the way to a condition.
_VARIANT_PLATFORM = re.compile(r"^(linux|osx|win)_")


@dataclass(frozen=True)
class CiSupport:
    """What `.ci_support` says about how a feedstock is built.

    Three answers out of one listing, wanted by different kinds of output of
    the same recipe: a noarch output needs the build floor, and an
    architecture-specific one needs the set of pythons, because that set *is*
    its matrix (DESIGN.md 3.3.1.1). The directory is fetched once either way.
    """

    #: ``(name, text)`` pairs, the shape `resolve_python_min` takes. One file,
    #: since `python_min` cannot differ per variant (DESIGN.md 3.3.3).
    files: tuple[tuple[str, str], ...] = ()
    #: The minor releases of python 3 this feedstock is built for, read off the
    #: variant names. Empty where it builds no python variants at all, or where
    #: conda-smithy has never rendered it.
    pythons: tuple[int, ...] = ()
    #: The platforms this feedstock is built for, read off the same names.
    #:
    #: For a `noarch: python` output this is the whole of the fourth build
    #: model: one platform means the ordinary single artifact, and more than
    #: one means conda-smithy's `noarch_platforms` is building the package
    #: once per platform, each artifact carrying the virtual package that
    #: names it. `conda-forge.yml` is where a person writes that down, but
    #: `.ci_support` is what conda-smithy actually rendered from it -- the
    #: same reason `python_min` is read here rather than from the recipe.
    platforms: tuple[str, ...] = ()
    #: The variant keys the rendered config carries, with `_` written as `-`
    #: so they read as package names: `netcdf_fortran` becomes
    #: `netcdf-fortran`. These are the packages conda-forge's global pinning
    #: supplies a version for, and a `host` line naming one takes no bound
    #: from swage (DESIGN.md 3.3.6).
    #:
    #: Every key, with no attempt to tell the ones naming packages from the
    #: ones that do not. `zip_keys`, `channel_sources` and `docker_image` are
    #: in here and are harmless: the set is only ever consulted by asking
    #: whether a package swage was about to bound is in it, and nothing is
    #: named those. A hand-maintained exclusion list would rot instead.
    pinned: frozenset[str] = frozenset()


def read_ci_support(github: GitHub, feedstock: str, ref: str) -> CiSupport:
    """The rendered build configs, as far as anything needs them.

    conda-smithy renders one file per build variant with the global pinning
    already folded in, so the listing alone says which pythons are built, and
    the first file answers `python_min` -- reading the rest is waste
    (DESIGN.md 3.3.3).
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
    """The packages conda-forge's global pinning supplies a version for.

    One file answers for all of them. conda-smithy renders a file per variant
    and they differ in the *values* -- `mpi: [mpich]` against `mpi: [openmpi]`
    -- and in the keys naming the build image rather than a package. Every
    `esmf` variant carries `hdf5`, `libnetcdf` and `netcdf_fortran`, which is
    the same reason `python_min` is read from the first file alone.
    """
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        # conda-smithy wrote it, so this does not happen; an unreadable file
        # means swage bounds a line the pinning would have, which is the
        # behavior it had before this existed rather than a new failure.
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
    """The platforms named by a set of variant file names, recipe spelling.

    Ordered as a recipe writes them rather than alphabetically, so a condition
    built from this reads the way the fleet's own recipes do.
    """
    found = {
        match.group(1) for name in names if (match := _VARIANT_PLATFORM.match(name))
    }
    return tuple(platform for platform in ("linux", "osx", "win") if platform in found)


def default_branch(github: GitHub, feedstock: str) -> str:
    """Which ref `swage audit` reads a feedstock at (DESIGN.md 8.2).

    Every other command is handed a ref by the pull request it is acting on.
    An audit has no pull request, so it has to ask -- and asking is the whole
    of why this exists, because the alternative is assuming. Most conda-forge
    feedstocks are on `main` and `scripts/compare_published.py` hardcodes it,
    which is fine for two families curated by hand and a silent wrong answer
    at fleet scale: a feedstock still on `master` would be read at a ref that
    does not exist, and report as unreadable rather than as whatever it is.

    One call per feedstock, which is affordable next to the archive an audit
    fetches for the same feedstock anyway.
    """
    repo = f"conda-forge/{feedstock}-feedstock"
    payload = github.api(f"repos/{repo}")
    if not isinstance(payload, Mapping):
        raise ForgeError(f"{repo}: repository metadata was not an object")
    branch = payload.get("default_branch")
    if not isinstance(branch, str) or not branch:
        raise ForgeError(f"{repo}: has no default branch")
    return branch
