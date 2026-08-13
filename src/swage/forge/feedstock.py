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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .errors import ForgeError, NotFound
from .github import GitHub

__all__ = ["FeedstockFiles", "read_ci_support", "read_feedstock"]

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


def read_ci_support(
    github: GitHub, feedstock: str, ref: str
) -> tuple[tuple[str, str], ...]:
    """One rendered build config, which is all `python_min` needs.

    conda-smithy renders one file per build variant with the global pinning
    already folded in, and `python_min` cannot differ per architecture -- so
    the first one read is the answer and reading the rest is waste
    (DESIGN.md 3.3.3). Returns `(name, text)` pairs, which is the shape
    `resolve_python_min` takes.
    """
    repo = f"conda-forge/{feedstock}-feedstock"
    try:
        listing = github.api(f"repos/{repo}/contents/{CI_SUPPORT}", {"ref": ref})
    except NotFound:
        # conda-smithy has never rendered this feedstock. The planner stops
        # rather than assuming a floor, and says so.
        return ()
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
        return ()
    return ((names[0], github.file(repo, f"{CI_SUPPORT}/{names[0]}", ref)),)
