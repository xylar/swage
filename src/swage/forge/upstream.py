"""Fetch the upstream metadata for one feedstock (v1 §3.6; DESIGN.md §6).

Which source applies is config: a file in a git tag, or the source archive the
recipe pins, with `upstream.metadata` naming the file where it is not at the
root. The version comes from the recipe, never from a query about what upstream
released.
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from dataclasses import replace
from pathlib import PurePosixPath

from swage.config import (
    ArchiveUpstream,
    CMakeUpstream,
    EsmfUpstream,
    FeedstockConfig,
    GitHubUpstream,
    ManualUpstream,
    NoUpstream,
)
from swage.mapping import normalize_name
from swage.recipe import Recipe, RecipeSource
from swage.upstream import (
    NothingToReconcile,
    RecipeUpstream,
    UpstreamError,
    UpstreamMetadata,
    parse_pyproject,
)
from swage.upstream.cmake import CMAKE_LISTS, CMAKE_MODULE, parse_cmake
from swage.upstream.esmf import COMMON_MK, VENDORED_PIO, parse_esmf
from swage.upstream.model import BUILD_SH

from .archive import (
    Fetcher,
    archive_named,
    archive_texts,
    download,
    metadata_texts,
    read_archive,
    verified_payload,
)
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
    ref: str = "",
) -> RecipeUpstream:
    """Read the metadata for the release or releases ``recipe`` builds.

    Raises `NothingToReconcile` where config says this feedstock packages no
    python distribution, before anything is fetched (v1 §4). ``ref`` is the
    commit the recipe was read at, for a reader whose declaration is partly
    in the feedstock itself.
    """
    upstream = config.upstream
    if isinstance(upstream, NoUpstream):
        raise NothingToReconcile(
            f"{config.feedstock} packages no python distribution: {upstream.reason}"
        )
    if isinstance(upstream, ManualUpstream):
        # Stopping here is the point: an empty declaration would report every
        # line as coming from nowhere (v1 §3.6.8).
        raise NothingToReconcile(
            f"swage does not read {config.feedstock}'s declaration: {upstream.reason}"
        )
    if isinstance(upstream, GitHubUpstream):
        return RecipeUpstream.of(
            _from_tag(recipe, config, upstream, github or GitHub())
        )
    if isinstance(upstream, EsmfUpstream):
        return RecipeUpstream.of(
            _from_esmf(recipe, config, github or GitHub(), fetch, ref)
        )
    if isinstance(upstream, CMakeUpstream):
        return RecipeUpstream.of(
            _from_cmake(recipe, config, upstream, github or GitHub(), fetch, ref)
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


def _from_cmake(
    recipe: Recipe,
    config: FeedstockConfig,
    upstream: CMakeUpstream,
    github: GitHub,
    fetch: Fetcher,
    ref: str,
) -> UpstreamMetadata:
    """A CMake project's declaration, joined across the archive and the
    feedstock (v1 §3.6.7): the whole `CMakeLists.txt` tree and the `.cmake`
    modules, and the build script at ``ref``.
    """
    url, sha256 = _one_source(recipe, config, "cmake")
    payload = verified_payload(url, sha256, fetch)
    tree = archive_named(payload, CMAKE_LISTS, url, suffix=CMAKE_MODULE)
    cmake_lists = tree.get(CMAKE_LISTS)
    if cmake_lists is None:
        raise ForgeError(
            f"{url}: has no {CMAKE_LISTS}\n"
            "  that file is where a CMake project states which packages it "
            "needs, and it is what `upstream: {source: cmake}` reads"
        )
    return parse_cmake(
        cmake_lists,
        github.file(
            f"conda-forge/{config.feedstock}-feedstock", BUILD_SH, ref or "HEAD"
        ),
        config.cmake_map,
        name=config.feedstock,
        version=recipe.context.get("version"),
        source=f"{url}::{CMAKE_LISTS}",
        supported=upstream.supported,
        skip=upstream.skip,
        tree=tree,
    )


def _one_source(
    recipe: Recipe, config: FeedstockConfig, reader: str
) -> tuple[str, str]:
    """The url and hash of the single archive a compiled project's reader
    reads; a recipe pinning several is refused.
    """
    sources = [
        (source.url, source.sha256)
        for source in archive_sources(recipe, config.feedstock)
        if source.url is not None and source.sha256 is not None
    ]
    if len(sources) != 1:
        raise ForgeError(
            f"{config.feedstock}: the {reader} reader wants one source and this "
            f"recipe has {len(sources)}"
        )
    return sources[0]


def _from_esmf(
    recipe: Recipe,
    config: FeedstockConfig,
    github: GitHub,
    fetch: Fetcher,
    ref: str,
) -> UpstreamMetadata:
    """ESMF's declaration, joined across the archive and the feedstock
    (v1 §3.6.6).
    """
    url, sha256 = _one_source(recipe, config, "esmf")
    payload = verified_payload(url, sha256, fetch)
    texts = archive_texts(payload, (COMMON_MK, VENDORED_PIO), url)
    common_mk = texts[COMMON_MK]
    if common_mk is None:
        raise ForgeError(
            f"{url}: has no {COMMON_MK}\n"
            "  that file is where ESMF states which libraries each of its "
            "build toggles links, and it is what `upstream: {source: esmf}` "
            "reads"
        )
    build_sh = github.file(
        f"conda-forge/{config.feedstock}-feedstock", BUILD_SH, ref or "HEAD"
    )
    return parse_esmf(
        common_mk,
        build_sh,
        config.link_map,
        version=recipe.context.get("version"),
        configure_ac=texts[VENDORED_PIO],
        source=f"{url}::{COMMON_MK}",
    )


def _by_output(
    recipe: Recipe,
    config: FeedstockConfig,
    releases: tuple[UpstreamMetadata, ...],
) -> dict[str, UpstreamMetadata]:
    """Which release each output of a several-source recipe reconciles against
    (v1 §3.6).

    An output draws on the release that declares its name; a metapackage is
    placed by `outputs[].upstream` (v1 §4). Two sources declaring the same
    project are ambiguous only where there are two outputs to tell apart;
    a single output takes the first.
    """
    by_name: dict[str, list[UpstreamMetadata]] = {}
    for release in releases:
        by_name.setdefault(normalize_name(release.name), []).append(release)
    ambiguous = sorted(name for name, found in by_name.items() if len(found) > 1)
    if ambiguous and len(recipe.outputs) > 1:
        # One output takes the first source declaring its name, below.
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
    # sentences.
    if misnamed:
        raise ForgeError(
            f"{config.feedstock}: {'; '.join(misnamed)} under "
            "outputs.<output>.upstream, which none of the recipe's sources "
            "declares\n"
            f"  they declare {declared}"
        )
    if unplaced:
        raise ForgeError(
            f"{config.feedstock}: the recipe builds from {len(releases)} sources\n"
            f"  nothing says which of them {', '.join(unplaced)} is built from\n"
            f"  the sources declare {declared}\n"
            "  name one of those in config under outputs.<output>.upstream"
        )
    return resolved


def fetch_upstream_texts(
    recipe: Recipe,
    config: FeedstockConfig,
    github: GitHub | None = None,
    fetch: Fetcher = download,
    ref: str = "",
) -> dict[str, str]:
    """The metadata files behind `fetch_upstream`, unparsed, by file name, for
    `draft` to quote (v1 §8.1).

    Re-fetched rather than carried. The wheel fallback is not followed; a
    reader-backed feedstock gets the files its reader read.
    """
    upstream = config.upstream
    if isinstance(upstream, GitHubUpstream):
        repo, path, tag = _tag_location(recipe, config, upstream)
        return {PurePosixPath(path).name: (github or GitHub()).file(repo, path, tag)}
    if isinstance(upstream, EsmfUpstream | CMakeUpstream):
        return _reader_texts(recipe, config, upstream, github or GitHub(), fetch, ref)
    if isinstance(upstream, ManualUpstream):
        # The whole workbench for these (v1 §3.6.8).
        return read_declaration(recipe, config, upstream, fetch)
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
        # Every source of a several-source recipe ships a file of the same name,
        # so the directory the recipe unpacks each into names them.
        prefix = f"{source.target_directory}/" if len(sources) > 1 else ""
        texts.update({f"{prefix}{name}": text for name, text in found.items()})
    return texts


def read_declaration(
    recipe: Recipe,
    config: FeedstockConfig,
    upstream: ManualUpstream,
    fetch: Fetcher = download,
) -> dict[str, str]:
    """The files config says upstream declares in, out of this release
    (v1 §3.6.8).

    Read rather than merely named, so a stale path is caught and a version
    bump can say which files moved. Every source is searched. A recipe with
    no archive to check against returns nothing rather than stopping; the
    caller says which paths went unchecked.
    """
    found: dict[str, str] = {}
    urls: list[str] = []
    for source in recipe.sources:
        if source.url is None or source.sha256 is None:
            continue
        urls.append(source.url)
        wanted = tuple(path for path in upstream.declares if path not in found)
        if not wanted:
            break
        payload = verified_payload(source.url, source.sha256, fetch)
        for path, text in archive_texts(payload, wanted, source.url).items():
            if text is not None:
                found[path] = text
    missing = [path for path in upstream.declares if path not in found]
    if missing and urls:
        raise ForgeError(
            f"{', '.join(urls)}: has no {', '.join(missing)}\n"
            "  `upstream.declares` names the files upstream states its "
            "dependencies in, relative to the archive's top-level directory\n"
            "  a path that has stopped being there means the declaration has "
            "moved, and is worth finding rather than dropping"
        )
    # In the order config named them, which is the order a reader opens them,
    # rather than the order the sources happened to yield them.
    return {path: found[path] for path in upstream.declares if path in found}


def moved_declarations(
    current: Mapping[str, str], previous: Mapping[str, str]
) -> tuple[str, ...]:
    """Which declared files differ between two releases, in config's order. A
    file this release added counts as moved.
    """
    return tuple(path for path, text in current.items() if previous.get(path) != text)


def _reader_texts(
    recipe: Recipe,
    config: FeedstockConfig,
    upstream: EsmfUpstream | CMakeUpstream,
    github: GitHub,
    fetch: Fetcher,
    ref: str,
) -> dict[str, str]:
    """Both halves of a reader's join, keyed by the path each one has upstream,
    at the same `ref` the reader used. Failures are left out rather than
    raised.
    """
    try:
        url, sha256 = _one_source(recipe, config, upstream.source)
        payload = verified_payload(url, sha256, fetch)
    except ForgeError:
        return {}
    wanted = (
        (COMMON_MK, VENDORED_PIO)
        if isinstance(upstream, EsmfUpstream)
        else (CMAKE_LISTS,)
    )
    texts = {
        name: text
        for name, text in archive_texts(payload, wanted, url).items()
        if text is not None
    }
    with contextlib.suppress(ForgeError):
        texts[BUILD_SH] = github.file(
            f"conda-forge/{config.feedstock}-feedstock", BUILD_SH, ref or "HEAD"
        )
    return texts


def _with_wheel_dependencies(
    metadata: UpstreamMetadata, fetch: Fetcher
) -> UpstreamMetadata:
    """Fill in dependencies from the wheel where the sdist stated none.

    Silence and emptiness are different claims (v1 §3.6.2): setuptools writes
    `Requires-Dist` only for a declarative project, and the wheel of the same
    release states the list. Fires only where the sdist states no requirement
    anywhere, core or extra; never corrects a list it did state.
    `build_requires` is kept from the archive. The scripts come along where
    the sdist was silent about them.
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
    scripts = wheel.entry_points if metadata.entry_points is None else None
    if not wheel.dependencies and not wheel.optional_dependencies:
        # The release really does need nothing. Recording a source for a list
        # that is empty either way would be provenance for a non-event.
        return metadata if scripts is None else replace(metadata, entry_points=scripts)
    return replace(
        metadata,
        dependencies=wheel.dependencies,
        optional_dependencies=wheel.optional_dependencies,
        dynamic_fields=wheel.dynamic_fields,
        dependency_source=filename,
        entry_points=metadata.entry_points if scripts is None else scripts,
    )


def archive_sources(recipe: Recipe, feedstock: str) -> tuple[RecipeSource, ...]:
    """The archives this recipe builds from, each with a URL and a hash
    (v1 §3.6). A source that is not a pinned URL stops the feedstock.
    """
    if not recipe.sources:
        raise ForgeError(f"{feedstock}: the recipe declares no source")
    unpinned = [
        source.target_directory or source.url or source.url_expr or "(no url)"
        for source in recipe.sources
        if source.url is None or source.sha256 is None
    ]
    if unpinned:
        raise ForgeError(
            f"{feedstock}: {', '.join(unpinned)} is not a URL with a sha256"
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
        parsed = parse_pyproject(text, f"{upstream.repo}/{path}@{tag}")
    except UpstreamError as exc:
        raise ForgeError(str(exc)) from exc
    # The path in the monorepo; `source` already carries the repo and the tag.
    return replace(parsed, declared_in=path)


def _tag_location(
    recipe: Recipe, config: FeedstockConfig, upstream: GitHubUpstream
) -> tuple[str, str, str]:
    """Which repo, path and tag this feedstock's metadata is read from: one
    answer for the fetch, the raw text and the report.
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
    """Where `fetch_upstream` read this feedstock's metadata from, as the report
    prints it (v1 §9.2).

    A recipe building several archives is named by its first. Empty where no
    source resolves to a URL.
    """
    upstream = config.upstream
    if isinstance(upstream, GitHubUpstream):
        repo, path, tag = _tag_location(recipe, config, upstream)
        return f"{repo}/{path}@{tag}"
    for source in recipe.sources:
        if source.url:
            return source.url
    return ""
