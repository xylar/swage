"""What swage says about a feedstock it does not read (v1 §3.6.8).

A `ManualUpstream` entry names the files a maintainer reconciles by hand. On a
pull request there are two releases to compare and the answer is `not-read` or
`declaration-moved`; on a default branch there is one, and the answer is where
to look. A named file the release no longer has is `declaration-moved` on
either (DESIGN.md §16).
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
    missing_declarations,
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
    """Whether the files swage points at moved, or where they are. ``pull`` is
    None on a default branch.
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
    """Whether the files swage points at moved, which is the whole answer here
    (v1 §3.6.8).

    A previous release swage cannot read leaves the comparison unmade and the
    feedstock merely unread, unless a named file is missing from this one.
    The config's own paragraph is the stop.
    """
    try:
        recipe = read_recipe(recipe_text)
        declared = read_declaration(recipe, config, upstream, fetch)
    except (ForgeError, RecipeError) as exc:
        return about("failed", stopped=str(exc))
    missing = missing_declarations(recipe, upstream, declared)

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
    if missing:
        # What the previous release had and this one lacks is in the diff
        # beside what changed, in config's order.
        moved = moved_declarations(declared, was or {})
        shown = tuple(
            path
            for path in upstream.declares
            if path in moved or (path in missing and path in (was or {}))
        )
        return about(
            "declaration-moved",
            reason=_gone(missing, version, moved, before),
            stopped=upstream.reason,
            declaration_diff=declaration_diff(
                declared,
                was or {},
                shown,
                before=before or "before",
                after=version or "after",
            ),
            **common,
        )
    # An empty `declared` is a source that is not a URL swage can fetch, and
    # takes the same answer as a previous release that could not be read.
    if was is None or not declared:
        named = upstream.declares
        return about(
            "not-read",
            reason=f"{', '.join(named)} could not be read out of both releases",
            stopped=upstream.reason,
            **common,
        )
    moved = moved_declarations(declared, was)
    if not moved:
        checked = tuple(declared)
        return about(
            "not-read",
            reason=(
                f"{', '.join(checked)} {'are' if len(checked) > 1 else 'is'} "
                f"unchanged {_between(before, version)}"
            ),
            stopped=upstream.reason,
            **common,
        )
    return about(
        "declaration-moved",
        reason=f"{', '.join(moved)} changed {_between(before, version)}",
        stopped=upstream.reason,
        # Labeled with the two releases, because this diff is read in a
        # terminal.
        declaration_diff=declaration_diff(
            declared,
            was,
            moved,
            before=before or "before",
            after=version or "after",
        ),
        **common,
    )


def _gone(
    missing: Sequence[str],
    version: str | None,
    moved: Sequence[str],
    before: str | None,
) -> str:
    """Which named files this release lacks, and which of the rest changed."""
    where = f"the {version} archive" if version else "this release's archive"
    several = len(missing) > 1
    reason = (
        f"{', '.join(missing)} {'are' if several else 'is'} not in {where}, "
        f"and `upstream.declares` still names {'them' if several else 'it'}"
    )
    if moved:
        reason += f", and {', '.join(moved)} changed {_between(before, version)}"
    return reason


def _between(before: str | None, version: str | None) -> str:
    """Which two releases were compared, named where the recipes name them."""
    if before and version:
        return f"from {before} to {version}"
    return "since the release this bump replaces"


def _declaration_metadata(
    feedstock: str, recipe: Recipe, declared: Iterable[str]
) -> UpstreamMetadata:
    """Enough of a release to report, and no dependencies: nothing reconciles
    against this. Named from config where the archive could not be read.
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
    """A feedstock swage does not read, reported as where to look instead
    (v1 §3.6.8).

    The declaration is read so a path upstream deleted is not pointed at:
    one the release lacks is `declaration-moved`, and anything else is
    `not-read`. Where the source is not a URL swage can fetch, the note says
    which paths went unchecked.
    """
    feedstock = config.feedstock
    try:
        recipe = read_recipe(recipe_text)
        declared = read_declaration(recipe, config, upstream, fetch)
    except (ForgeError, RecipeError) as exc:
        return about("failed", stopped=str(exc), notes=notes)
    missing = missing_declarations(recipe, upstream, declared)
    if missing:
        return about(
            "declaration-moved",
            reason=_gone(missing, recipe.context.get("version"), (), None),
            stopped=upstream.reason,
            notes=notes,
            upstream_source=upstream_location(recipe, config),
            upstream=RecipeUpstream.of(
                _declaration_metadata(feedstock, recipe, declared)
            ),
        )
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
        reason=f"declared in {', '.join(upstream.declares)}, which swage does not read",
        stopped=upstream.reason,
        notes=notes,
        upstream_source=upstream_location(recipe, config),
        upstream=RecipeUpstream.of(
            _declaration_metadata(feedstock, recipe, declared or upstream.declares)
        ),
    )
