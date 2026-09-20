"""What swage says about a feedstock it does not read (v1 §3.6.8).

A `ManualUpstream` entry names the files a maintainer reconciles by hand --
a `configure.ac`, a `CMakeLists.txt` -- and swage cannot say what they mean.
What it can say without any vocabulary is whether those files are the ones
the recipe was last reconciled against, and that is the whole of what these
two records carry. On a pull request there are two releases to compare and
the answer is `not-read` or `declaration-moved`; on a default branch there
is one, and the answer is where to look.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any

from swage.config import FeedstockConfig, ManualUpstream
from swage.forge import (
    RECIPE_V1,
    BotPullRequest,
    Fetcher,
    ForgeError,
    GitHub,
    moved_declarations,
    read_declaration,
    upstream_location,
)
from swage.recipe import Recipe, RecipeError, read_recipe
from swage.run import Record, declaration_diff
from swage.upstream import RecipeUpstream, UpstreamMetadata

__all__ = ["declaration_record"]


def declaration_record(
    about: Callable[..., Record],
    github: GitHub,
    config: FeedstockConfig,
    upstream: ManualUpstream,
    pull: BotPullRequest | None,
    recipe_text: str,
    fetch: Fetcher,
    notes: tuple[str, ...] = (),
) -> Record:
    """Whether the files swage points at moved, or where they are.

    ``about`` is the pipeline's recorder for this subject; ``pull`` is None on
    a default branch, where the recipe and upstream name the same release and
    nothing can have moved since.
    """
    if pull is None:
        return _not_read(about, config, upstream, recipe_text, notes, fetch)
    return _declaration_record(
        about, github, config, upstream, pull, recipe_text, fetch
    )


def _declaration_record(
    about: Callable[..., Record],
    github: GitHub,
    config: FeedstockConfig,
    upstream: ManualUpstream,
    pull: BotPullRequest,
    recipe_text: str,
    fetch: Fetcher,
) -> Record:
    """Whether the files swage points at moved, which is the whole answer here.

    swage cannot say what a `configure.ac` means, and does not try. What it can
    say without any vocabulary at all is whether the file is the same one the
    recipe was last reconciled against -- and "these two of your four
    declaration files changed in this release" is the honest form of "your
    dependencies may have moved" (design-v1.md 3.6.8).

    A previous release swage cannot read leaves the comparison unmade rather
    than assuming either answer, and the feedstock reports as merely unread.
    That is the same direction every other unclassifiable case falls in: a
    missing comparison must not manufacture a finding any more than it should
    suppress one.

    **Three details, because there are three answers**, and the two that end in
    NOT READ are not the same answer at all: one says the files are the same
    ones in both releases, and the other says nothing could be compared. Both
    used to print the config's reason alone, so a bump that had been checked
    and one that could not be read alike said only that swage does not read
    this feedstock -- and the check, which is the whole of what swage has to
    offer here, went unmentioned in the case where it had passed.
    """
    try:
        recipe = read_recipe(recipe_text)
        declared = read_declaration(recipe, config, upstream, fetch)
    except (ForgeError, RecipeError) as exc:
        return about("failed", stopped=str(exc))

    was: dict[str, str] | None = None
    before: str | None = None
    try:
        base = read_recipe(github.file(pull.repo, RECIPE_V1, pull.base_ref))
        was = read_declaration(base, config, upstream, fetch)
        before = base.context.get("version")
    except (ForgeError, RecipeError):
        was = None

    version = recipe.context.get("version")
    common: dict[str, Any] = {
        "upstream": RecipeUpstream.of(
            UpstreamMetadata(
                name=config.feedstock,
                version=version,
                declared_in=" + ".join(declared),
            )
        ),
        "upstream_source": upstream_location(recipe, config),
        "previous": before,
    }
    # An empty `declared` is the feedstock whose source is not a URL swage can
    # fetch -- `r-proj4` builds from a list of CRAN mirrors -- so there is
    # nothing to compare on this side either, and it takes the same answer as a
    # previous release that could not be read.
    if was is None or not declared:
        named = upstream.declares
        return about(
            "not-read",
            reason=(
                f"{', '.join(named)} could not be read out of both releases, "
                f"so nothing says whether {_them(named)} moved in this bump "
                f"-- {upstream.reason}"
            ),
            **common,
        )
    moved = moved_declarations(declared, was)
    if not moved:
        checked = tuple(declared)
        return about(
            "not-read",
            reason=(
                f"{', '.join(checked)} {'are' if len(checked) > 1 else 'is'} "
                f"unchanged {_between(before, version)} -- {upstream.reason}"
            ),
            **common,
        )
    return about(
        "declaration-moved",
        reason=(
            f"{', '.join(moved)} changed {_between(before, version)}, and "
            f"swage does not read {_them(moved)} -- {upstream.reason}"
        ),
        # Labeled with the two releases rather than with a directory layout:
        # this diff is read in a terminal, where nothing else on the screen
        # says which side is which.
        declaration_diff=declaration_diff(
            declared,
            was,
            moved,
            before=before or "before",
            after=version or "after",
        ),
        **common,
    )


def _them(files: Sequence[str]) -> str:
    """`it` or `them`, for a list whose length the sentence has already given."""
    return "them" if len(files) > 1 else "it"


def _between(before: str | None, version: str | None) -> str:
    """Which two releases were compared, named where the recipes name them.

    A recipe whose context sets no version leaves this as a phrase rather than
    a pair, because "unchanged" without saying since when is the one form of
    this sentence that could be read as a claim about the recipe.
    """
    if before and version:
        return f"from {before} to {version}"
    return "since the release this bump replaces"


def _declaration_metadata(
    feedstock: str, recipe: Recipe, declared: Iterable[str]
) -> UpstreamMetadata:
    """Enough of a release to report, and deliberately no dependencies at all.

    `declared_in` is the whole payload: the report's job here is to name the
    files, and an empty `dependencies` is not a claim that upstream needs
    nothing -- nothing reconciles against this, because reaching it means the
    plan was refused before it started.

    Named from config where the archive could not be read, so a feedstock with
    no fetchable source still says which files to open. The note beside it is
    what keeps that from reading as a checked answer.
    """
    return UpstreamMetadata(
        name=feedstock,
        version=recipe.context.get("version"),
        declared_in=" + ".join(declared),
    )


def _not_read(
    about: Callable[..., Record],
    config: FeedstockConfig,
    upstream: ManualUpstream,
    recipe_text: str,
    notes: tuple[str, ...],
    fetch: Fetcher,
) -> Record:
    """A feedstock swage does not read, reported as where to look instead.

    The declaration is read even though nothing is parsed from it, because a
    path that has stopped being in the archive is the one thing here that can
    be wrong, and pointing a maintainer at a file upstream deleted two releases
    ago is worse than saying nothing (design-v1.md 3.6.8).

    Always `not-read` rather than `declaration-moved`: an audit reads the
    default branch, where the recipe and upstream name the same release, so
    there is no second release to compare against and nothing can have moved
    since. `scan` and `update` are where the comparison happens, because they
    are driven by a bump.

    Where the recipe's source is not a URL swage can fetch there is no archive
    to check the paths against, and the note says which ones went unchecked --
    `r-proj4` builds from a list of CRAN mirrors. Saying nothing would let a
    checked pointer and an unchecked one read alike.
    """
    feedstock = config.feedstock
    try:
        recipe = read_recipe(recipe_text)
        declared = read_declaration(recipe, config, upstream, fetch)
    except (ForgeError, RecipeError) as exc:
        return about("failed", stopped=str(exc), notes=notes)
    unchecked = tuple(path for path in upstream.declares if path not in declared)
    if unchecked:
        notes = (
            *notes,
            f"{', '.join(unchecked)} could not be checked against the "
            "release: this recipe's source is not a URL swage can fetch, so "
            "the files are named from config and nothing confirms they are "
            "still there",
        )
    return about(
        "not-read",
        reason=upstream.reason,
        notes=notes,
        upstream_source=upstream_location(recipe, config),
        upstream=RecipeUpstream.of(
            _declaration_metadata(feedstock, recipe, declared or upstream.declares)
        ),
    )
