"""Fetch the upstream metadata for one feedstock (DESIGN.md 3.6).

Two sources reach swage, and which one applies is config, not detection:

**A file in a git tag** -- the airflow-providers path. Each provider release is
a tag in the `apache/airflow` monorepo carrying that provider's
`pyproject.toml`, so there is no sdist to fetch and the file is read straight
through the contents API. The family writes the tag and path as templates
(DESIGN.md 4), because 99 feedstocks share one rule.

**A source archive** -- the google-cloud path, and the default. The recipe
already names the archive and pins its hash, so this needs no configuration at
all beyond saying which path applies. Where the metadata inside that archive
is not at the root, `upstream.metadata` says where it is: `openlineage-python`
pins a tarball of a monorepo carrying seven `pyproject.toml` files, and which
subdirectory holds the package is not something swage can infer.

**The version comes from the recipe, never from a query about what upstream
released.** For the archive path it is baked into the URL the bot wrote. For
the tag path it is the recipe's own `context.version`. Either way swage
reconciles against the release this pull request is proposing, rather than
against whatever is newest by the time swage happens to look -- the same
reasoning as `python_min` (DESIGN.md 3.3.3), and it removes a whole class of
race rather than handling it.
"""

from __future__ import annotations

from swage.config import ArchiveUpstream, FeedstockConfig, GitHubUpstream
from swage.recipe import Recipe, RecipeSource
from swage.upstream import UpstreamError, UpstreamMetadata, parse_pyproject

from .archive import Fetcher, download, read_archive
from .errors import ForgeError
from .github import GitHub

__all__ = ["fetch_upstream", "sole_source", "upstream_location"]


def fetch_upstream(
    recipe: Recipe,
    config: FeedstockConfig,
    github: GitHub | None = None,
    fetch: Fetcher = download,
) -> UpstreamMetadata:
    """Read the metadata for the release ``recipe`` builds."""
    upstream = config.upstream
    if isinstance(upstream, GitHubUpstream):
        return _from_tag(recipe, config, upstream, github or GitHub())
    source = sole_source(recipe, config.feedstock)
    if source.url is None or source.sha256 is None:
        raise ForgeError(
            f"{config.feedstock}: the recipe's source is not a URL with a "
            "sha256, so there is no archive to read upstream metadata from"
        )
    return read_archive(
        source.url,
        source.sha256,
        fetch,
        metadata=upstream.metadata if isinstance(upstream, ArchiveUpstream) else None,
    )


def sole_source(recipe: Recipe, feedstock: str) -> RecipeSource:
    """The one archive this recipe builds from, or a refusal naming them all.

    A recipe with several sources builds one package out of more than one
    release, and which of them a given output's dependencies should be
    reconciled against is not something the recipe says. `airflow-feedstock`
    is the case: three sdists with two independent versions, told apart only
    by `target_directory`. Answering it means per-output upstream metadata,
    which the planner does not have yet, so swage stops and names them rather
    than reconciling everything against whichever came first.
    """
    if not recipe.sources:
        raise ForgeError(
            f"{feedstock}: the recipe declares no source, so there is no "
            "upstream release to reconcile against"
        )
    if len(recipe.sources) > 1:
        listed = "\n".join(
            f"    {source.target_directory or '(no target_directory)'}: "
            f"{source.url or source.url_expr}"
            for source in recipe.sources
        )
        raise ForgeError(
            f"{feedstock}: the recipe builds from {len(recipe.sources)} sources\n"
            f"{listed}\n"
            "  swage reconciles one output against one release and cannot tell "
            "which of these an output's dependencies belong to\n"
            "  update this feedstock by hand"
        )
    return recipe.sources[0]


def _from_tag(
    recipe: Recipe,
    config: FeedstockConfig,
    upstream: GitHubUpstream,
    github: GitHub,
) -> UpstreamMetadata:
    version = recipe.context.get("version")
    if not version:
        raise ForgeError(
            f"{config.feedstock}: upstream metadata comes from a "
            f"{upstream.repo} tag, but the recipe's context sets no version to "
            "build that tag from"
        )
    fields = _fields(config, version)
    try:
        tag = upstream.tag.format(**fields)
        path = upstream.metadata.format(**fields)
    except KeyError as exc:
        raise ForgeError(
            f"config/families/{config.family}.yaml: upstream tag or metadata "
            f"names {exc} , which swage does not substitute; it knows "
            f"{', '.join(sorted(fields))}"
        ) from exc

    text = github.file(upstream.repo, path, tag)
    try:
        return parse_pyproject(text, f"{upstream.repo}/{path}@{tag}")
    except UpstreamError as exc:
        raise ForgeError(str(exc)) from exc


def _fields(config: FeedstockConfig, version: str) -> dict[str, str]:
    return {
        "slug": config.slug,
        # `apache-hive` names `providers/apache/hive/` in the monorepo.
        "slug_path": config.slug.replace("-", "/"),
        "version": version,
    }


def upstream_location(recipe: Recipe, config: FeedstockConfig) -> str:
    """Where `fetch_upstream` read this feedstock's metadata from.

    The report prints this, and DESIGN.md 9.2 wants a location rather than
    prose there -- "why did this dependency appear" is a question whose next
    step should be opening the file that said so. It is derived from the same
    fields the fetch used rather than described separately, so the two cannot
    disagree about which release was read.

    Called only after a fetch has succeeded, so the cases that would stop a
    feedstock -- several sources, no version -- have already been refused.
    """
    upstream = config.upstream
    if isinstance(upstream, GitHubUpstream):
        fields = _fields(config, recipe.context.get("version", ""))
        return (
            f"{upstream.repo}/{upstream.metadata.format(**fields)}"
            f"@{upstream.tag.format(**fields)}"
        )
    return sole_source(recipe, config.feedstock).url or ""
