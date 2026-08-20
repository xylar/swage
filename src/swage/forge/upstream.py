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

from dataclasses import replace
from pathlib import PurePosixPath

from swage.config import ArchiveUpstream, FeedstockConfig, GitHubUpstream
from swage.mapping import normalize_name
from swage.recipe import Recipe, RecipeSource
from swage.upstream import (
    RecipeUpstream,
    UpstreamError,
    UpstreamMetadata,
    parse_pyproject,
)

from .archive import Fetcher, download, metadata_texts, read_archive, verified_payload
from .errors import ForgeError
from .github import GitHub
from .wheel import wheel_metadata

__all__ = [
    "archive_sources",
    "fetch_upstream",
    "fetch_upstream_texts",
    "upstream_location",
]


def fetch_upstream(
    recipe: Recipe,
    config: FeedstockConfig,
    github: GitHub | None = None,
    fetch: Fetcher = download,
) -> RecipeUpstream:
    """Read the metadata for the release or releases ``recipe`` builds."""
    upstream = config.upstream
    if isinstance(upstream, GitHubUpstream):
        return RecipeUpstream.of(
            _from_tag(recipe, config, upstream, github or GitHub())
        )
    releases = tuple(
        _with_wheel_dependencies(
            read_archive(
                source.url,
                source.sha256,
                fetch,
                metadata=(
                    upstream.metadata if isinstance(upstream, ArchiveUpstream) else None
                ),
            ),
            fetch,
        )
        for source in archive_sources(recipe, config.feedstock)
        # Narrowed by `archive_sources`; repeated for the type checker.
        if source.url is not None and source.sha256 is not None
    )
    if len(releases) == 1:
        return RecipeUpstream.of(releases[0])
    return RecipeUpstream(
        releases=releases, by_output=_by_output(recipe, config, releases)
    )


def _by_output(
    recipe: Recipe,
    config: FeedstockConfig,
    releases: tuple[UpstreamMetadata, ...],
) -> dict[str, UpstreamMetadata]:
    """Which release each output of a several-source recipe reconciles against.

    **An output draws on the release that declares its name.** `airflow`'s
    `apache-airflow-core` output builds the `apache-airflow-core` sdist, and
    the archive says so itself -- so this is a fact read out of the metadata
    rather than a guess about which source came first, and it needs nothing
    written down for the outputs that are the packages upstream publishes.

    The rest are metapackages, which upstream has no distribution for and
    swage therefore cannot place: `airflow-with-all` folds in the extras of
    `apache-airflow` and `apache-airflow-core-with-all` those of
    `apache-airflow-core`, and nothing in either recipe or metadata
    distinguishes the two. `outputs[].upstream` is where that is stated
    (DESIGN.md 4).
    """
    by_name: dict[str, list[UpstreamMetadata]] = {}
    for release in releases:
        by_name.setdefault(normalize_name(release.name), []).append(release)
    ambiguous = sorted(name for name, found in by_name.items() if len(found) > 1)
    if ambiguous:
        raise ForgeError(
            f"{config.feedstock}: {len(releases)} of the recipe's sources "
            f"declare the same project, {', '.join(ambiguous)}\n"
            "  swage tells one output's release from another's by the name "
            "the archive declares, and these do not tell each other apart\n"
            "  update this feedstock by hand"
        )

    resolved: dict[str, UpstreamMetadata] = {}
    unplaced: list[str] = []
    misnamed: list[str] = []
    for output in recipe.outputs:
        name = output.name or ""
        stated = config.outputs[name].upstream if name in config.outputs else None
        found = by_name.get(normalize_name(stated or name))
        if found is not None:
            resolved[name] = found[0]
        elif stated is not None:
            misnamed.append(f"{name} names {stated}")
        else:
            unplaced.append(output.name or output.name_expr or "(unnamed output)")
    declared = ", ".join(sorted(by_name))
    # A wrong answer and no answer are different mistakes and get different
    # sentences: pointing a maintainer at the key they already wrote is advice
    # about the wrong thing.
    if misnamed:
        raise ForgeError(
            f"{config.feedstock}: {'; '.join(misnamed)} under "
            "outputs.<output>.upstream, which none of the recipe's sources "
            "declares\n"
            f"  they declare {declared}"
        )
    if unplaced:
        raise ForgeError(
            f"{config.feedstock}: the recipe builds from {len(releases)} "
            f"sources and nothing says which of them "
            f"{', '.join(unplaced)} is built from\n"
            f"  the sources declare {declared}\n"
            "  name one of those in config under outputs.<output>.upstream"
        )
    return resolved


def fetch_upstream_texts(
    recipe: Recipe,
    config: FeedstockConfig,
    github: GitHub | None = None,
    fetch: Fetcher = download,
) -> dict[str, str]:
    """The metadata files behind `fetch_upstream`, unparsed, by file name.

    `draft` quotes these back at the maintainer (DESIGN.md 8.1), and it
    re-fetches rather than having `UpstreamMetadata` carry the raw text:
    ~50 KB per feedstock dragged through a 487-feedstock sweep to serve one
    interactive command is the wrong trade, and nothing else wants it.

    The wheel fallback is deliberately not followed. Where an sdist states no
    dependencies, `fetch_upstream` reads them out of the wheel instead, and
    that file is a zip of a built distribution rather than something to put in
    front of a reader. What the workbench shows is what the recipe's own
    archive contains, and `UpstreamMetadata.dependency_source` already names
    the wheel where one was used.
    """
    upstream = config.upstream
    if isinstance(upstream, GitHubUpstream):
        repo, path, tag = _tag_location(recipe, config, upstream)
        return {PurePosixPath(path).name: (github or GitHub()).file(repo, path, tag)}
    sources = archive_sources(recipe, config.feedstock)
    texts: dict[str, str] = {}
    for source in sources:
        if source.url is None or source.sha256 is None:
            continue
        found = metadata_texts(
            verified_payload(source.url, source.sha256, fetch),
            source.url,
            metadata=(
                upstream.metadata if isinstance(upstream, ArchiveUpstream) else None
            ),
        )
        # Every source of a several-source recipe ships a file of the same
        # name, so the workbench would show one `PKG-INFO` and hide the other
        # two. The directory the recipe unpacks each archive into is what the
        # recipe itself uses to tell them apart, so it is what names them here.
        prefix = f"{source.target_directory}/" if len(sources) > 1 else ""
        texts.update({f"{prefix}{name}": text for name, text in found.items()})
    return texts


def _with_wheel_dependencies(
    metadata: UpstreamMetadata, fetch: Fetcher
) -> UpstreamMetadata:
    """Fill in dependencies from the wheel where the sdist stated none.

    **Silence and emptiness are different claims**, the distinction DESIGN.md
    3.6.2 already draws for `[build-system] requires`, and here it decides
    whether a recipe's dependencies can be explained at all. An sdist whose
    `PKG-INFO` carries no `Requires-Dist` has usually not said "this package
    needs nothing"; it has said nothing, because setuptools writes that field
    only for a project that declares its dependencies declaratively. The wheel
    of the same release is built after `setup.py` has run and states them.

    swage cannot tell the two apart from the sdist alone, and does not have to:
    asking the wheel costs one request and the answers only ever agree or fill
    a gap. A release that genuinely needs nothing has a wheel that says so, and
    nothing changes.

    Deliberately narrow. It fires only where the sdist states no requirement
    at all -- never to correct, extend or second-guess a list it did state.
    Two distributions of one release disagreeing about their dependencies is a
    broken release, not something for swage to arbitrate unattended.

    **Naming an extra is not stating a requirement**, and reading it as one
    hid whole dependency lists. setuptools writes `Provides-Extra` from the
    keys of `extras_require` and `Requires-Dist` only for a project that
    declares dependencies declaratively, so a `setup.py` project with extras
    produces a `PKG-INFO` that names them and states nothing else --
    `flask-appbuilder` 5.2.2 names four and carries no `Requires-Dist`, while
    its wheel declares the 21 runtime dependencies its recipe already has.
    Keying on "no dependencies and no extras" skipped exactly those releases,
    which is why the condition is now that no requirement is stated anywhere:
    not in the core list, and not inside any extra.

    `build_requires` is kept from the archive throughout. Core metadata carries
    no build-system table (3.6.2), so the wheel has nothing to say about `host`
    and must not be allowed to blank it.
    """
    if metadata.dependencies or any(metadata.optional_dependencies.values()):
        return metadata
    if not metadata.name or not metadata.version:
        # Nothing to look the release up by. A PKG-INFO this thin is not one
        # swage can do better with.
        return metadata

    found = wheel_metadata(metadata.name, metadata.version, fetch)
    if found is None:
        return metadata
    wheel, filename = found
    if not wheel.dependencies and not wheel.optional_dependencies:
        # The release really does need nothing. Recording a source for a list
        # that is empty either way would be provenance for a non-event.
        return metadata
    return replace(
        metadata,
        dependencies=wheel.dependencies,
        optional_dependencies=wheel.optional_dependencies,
        dynamic_fields=wheel.dynamic_fields,
        dependency_source=filename,
    )


def archive_sources(recipe: Recipe, feedstock: str) -> tuple[RecipeSource, ...]:
    """The archives this recipe builds from, each with a URL and a hash.

    Almost every recipe has one. A few build several: `airflow-feedstock`
    packages three sdists at two independent versions, and `_by_output` is
    what decides which of them each output reconciles against.

    A source that is not a pinned URL stops the feedstock, because there is
    then no archive to read and no way to verify what was read against what
    the recipe claims to build (DESIGN.md 3.6).
    """
    if not recipe.sources:
        raise ForgeError(
            f"{feedstock}: the recipe declares no source, so there is no "
            "upstream release to reconcile against"
        )
    unpinned = [
        source.target_directory or source.url or source.url_expr or "(no url)"
        for source in recipe.sources
        if source.url is None or source.sha256 is None
    ]
    if unpinned:
        raise ForgeError(
            f"{feedstock}: {', '.join(unpinned)} is not a URL with a sha256, "
            "so there is no archive to read upstream metadata from"
        )
    return recipe.sources


def _from_tag(
    recipe: Recipe,
    config: FeedstockConfig,
    upstream: GitHubUpstream,
    github: GitHub,
) -> UpstreamMetadata:
    repo, path, tag = _tag_location(recipe, config, upstream)
    text = github.file(repo, path, tag)
    try:
        return parse_pyproject(text, f"{upstream.repo}/{path}@{tag}")
    except UpstreamError as exc:
        raise ForgeError(str(exc)) from exc


def _tag_location(
    recipe: Recipe, config: FeedstockConfig, upstream: GitHubUpstream
) -> tuple[str, str, str]:
    """Which repo, path and tag this feedstock's metadata is read from.

    One answer for the three callers that need it -- the fetch, the raw text
    `draft` writes, and the location the report prints -- because a workbench
    quoting a different tag from the one that was reconciled against would be
    a workbench answering about a different release.
    """
    version = recipe.context.get("version")
    if not version:
        raise ForgeError(
            f"{config.feedstock}: upstream metadata comes from a "
            f"{upstream.repo} tag, but the recipe's context sets no version to "
            "build that tag from"
        )
    fields = _fields(config, version)
    try:
        return (
            upstream.repo,
            upstream.metadata.format(**fields),
            upstream.tag.format(**fields),
        )
    except KeyError as exc:
        raise ForgeError(
            f"config/families/{config.family}.yaml: upstream tag or metadata "
            f"names {exc} , which swage does not substitute; it knows "
            f"{', '.join(sorted(fields))}"
        ) from exc


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
    feedstock -- an unpinned source, no version -- have already been refused.

    A recipe building several archives is named by its first, which is the one
    `RecipeUpstream.primary` reports and the one the version in the pull
    request title came from. `swage draft` writes every source's metadata out,
    so the reader who needs the other two has them.
    """
    upstream = config.upstream
    if isinstance(upstream, GitHubUpstream):
        repo, path, tag = _tag_location(recipe, config, upstream)
        return f"{repo}/{path}@{tag}"
    return archive_sources(recipe, config.feedstock)[0].url or ""
