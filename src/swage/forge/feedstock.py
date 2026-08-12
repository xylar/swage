"""Read a feedstock's files at one commit (DESIGN.md 3.5).

Everything the planner needs about a feedstock comes out of the pull request
being read, at the pull request's own head: the recipe, the build floor, and
the variant config that decides whether the feedstock is in scope at all. None
of it is fetched from anywhere else and none of it needs a clone -- at several
hundred feedstocks, cloning to read is untenable, and only a feedstock that
actually needs a commit is ever cloned.

**v0 is routed, not parsed.** Most of the fleet still carries `meta.yaml`, and
a v0 recipe does not parse as YAML at all -- `{{ name }}` at the start of a
value opens a flow mapping. Surfacing that as "invalid YAML" would make the
single most common condition in the fleet look like a corrupt file, so the
filename is checked first and the feedstock is reported as needing migration
(DESIGN.md 3.1).

**`.ci_support` is read only when the recipe does not answer.** `python_min`
comes from the recipe's own `context.python_min` where it sets one, and only
otherwise from a rendered `.ci_support` file (DESIGN.md 3.3.3). Reading the
directory unconditionally would spend two API calls per feedstock to learn
something the first call already contained.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .errors import ForgeError, NotFound
from .github import GitHub

__all__ = ["FeedstockFiles", "read_feedstock"]

RECIPE_V1 = "recipe/recipe.yaml"
RECIPE_V0 = "recipe/meta.yaml"
CONDA_BUILD_CONFIG = "recipe/conda_build_config.yaml"
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
    #: `recipe/conda_build_config.yaml`, where the feedstock has one. The
    #: precondition check needs it to spot a build-variant switch
    #: (DESIGN.md 3.3.5), and most feedstocks do not have one.
    conda_build_config: str | None = None
    #: `(name, text)` pairs, as `resolve_python_min` wants them. Empty unless
    #: the recipe failed to answer for itself.
    ci_support: tuple[tuple[str, str], ...] = field(default=())

    @property
    def repo(self) -> str:
        return f"conda-forge/{self.feedstock}-feedstock"


def read_feedstock(
    github: GitHub, feedstock: str, ref: str, ci_support: bool = False
) -> FeedstockFiles:
    """Read what the planner needs, at ``ref``.

    ``ci_support`` asks for the rendered build config as well. It is off by
    default because it costs two more calls and most recipes set their own
    `context.python_min`; the caller turns it on after reading the recipe and
    finding that this one does not.
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

    return FeedstockFiles(
        feedstock=feedstock,
        ref=ref,
        recipe=recipe,
        conda_build_config=_optional(github, repo, CONDA_BUILD_CONFIG, ref),
        ci_support=_ci_support(github, repo, ref) if ci_support else (),
    )


def _optional(github: GitHub, repo: str, path: str, ref: str) -> str | None:
    """A file the feedstock may simply not have, which is not a failure."""
    try:
        return github.file(repo, path, ref)
    except NotFound:
        return None


def _ci_support(github: GitHub, repo: str, ref: str) -> tuple[tuple[str, str], ...]:
    """One rendered build config, which is all `python_min` needs.

    conda-smithy renders one file per build variant with the global pinning
    already folded in, and `python_min` cannot differ per architecture -- so
    the first one read is the answer and reading the rest is waste
    (DESIGN.md 3.3.3).
    """
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
