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
    python distribution. That is checked before anything is fetched, because
    the failure it prevents is *successful*: a feedstock in that state has a
    source archive carrying some other component's metadata, and reading it
    produces a confident plan for the wrong project (DESIGN.md 4).

    ``ref`` is the commit the recipe was read at, and is needed only by a
    reader whose declaration is partly in the feedstock itself -- `esmf`'s
    toggles are in `recipe/build.sh`, and reading them at the default branch
    while the recipe came from a pull request would join two different
    commits.
    """
    upstream = config.upstream
    if isinstance(upstream, NoUpstream):
        raise NothingToReconcile(
            f"{config.feedstock} packages no python distribution: {upstream.reason}"
        )
    if isinstance(upstream, ManualUpstream):
        # Stopping here is the point. Returning an empty declaration instead
        # would have the planner report every line of a real recipe as coming
        # from nowhere, which is worse than saying plainly that swage does not
        # read this (DESIGN.md 3.6.8).
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
    """A CMake project's declaration, joined across the archive and the feedstock.

    The same two reads `_from_esmf` makes and for the same reason: the
    top-level `CMakeLists.txt` says what each `option(...)` implies, and the
    feedstock's own build script says which of them the `-D` flags turn on
    (DESIGN.md 3.6.7).

    The whole `CMakeLists.txt` tree goes with the top-level file, because a
    project of any size states its dependencies in the directory that uses
    them and the reader follows `add_subdirectory` to reach them. The `.cmake`
    modules go too, because a project may state them in one and `include()` it
    -- `netcdf-c` puts eighteen `find_package` calls in a single such file.
    One pass over the archive that is already in memory, and which files the
    walk ends up reading is the reader's decision rather than this one's.

    The build script is read at ``ref``, which is the commit the recipe came
    from. Reading it at the default branch while the recipe came from a pull
    request would join two different commits, and the flags are exactly what
    a pull request editing the build might have changed.
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
    """The url and hash of the single archive a compiled project's reader reads.

    A reader of this kind joins one source tree against one build script, so a
    recipe pinning several is one it has nothing to say about -- which of them
    declares the dependencies is a question no file answers.
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
    """ESMF's declaration, joined across the archive and the feedstock.

    Two reads rather than one, because neither file is the declaration by
    itself: `build/common.mk` says which libraries each toggle implies, and
    the feedstock's own `recipe/build.sh` says which toggles are on
    (DESIGN.md 3.6.6).
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

    **Two sources declaring the same project are ambiguous only where there
    are two outputs to tell apart.** `authlib` builds one package from the
    PyPI sdist and unpacks the GitHub tag beside it for the test suite, so
    both archives declare `authlib` at the same version -- and the refusal
    below fired on a question that does not arise, because a single output has
    exactly one release to reconcile against whichever way it is chosen. It is
    the first source, which is the archive the recipe builds and the one every
    other part of swage already calls the primary. That output still has to be
    named for a release that exists -- the refusal below for an output nothing
    places is what catches a single-output recipe whose sources declare some
    other project.
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
    ref: str = "",
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

    **A reader-backed feedstock gets the files its reader read**, which is the
    whole reason a reader exists: the maintainer coming back after months does
    not need to be told the dependencies, they need to be told where upstream
    states them (DESIGN.md 3.6.6). Falling through to the metadata search here
    showed `esmf` the `pyproject.toml` of the separate `esmpy` project, and
    `proj.4` nothing at all -- a workbench that is wrong and one that is empty,
    on exactly the two feedstocks whose declaration is hardest to find by hand.
    """
    upstream = config.upstream
    if isinstance(upstream, GitHubUpstream):
        repo, path, tag = _tag_location(recipe, config, upstream)
        return {PurePosixPath(path).name: (github or GitHub()).file(repo, path, tag)}
    if isinstance(upstream, EsmfUpstream | CMakeUpstream):
        return _reader_texts(recipe, config, upstream, github or GitHub(), fetch, ref)
    if isinstance(upstream, ManualUpstream):
        # The whole workbench for these: swage has no plan to show and no
        # findings to raise, so the files are what there is to put in front of
        # somebody (DESIGN.md 3.6.8).
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
        # Every source of a several-source recipe ships a file of the same
        # name, so the workbench would show one `PKG-INFO` and hide the other
        # two. The directory the recipe unpacks each archive into is what the
        # recipe itself uses to tell them apart, so it is what names them here.
        prefix = f"{source.target_directory}/" if len(sources) > 1 else ""
        texts.update({f"{prefix}{name}": text for name, text in found.items()})
    return texts


def read_declaration(
    recipe: Recipe,
    config: FeedstockConfig,
    upstream: ManualUpstream,
    fetch: Fetcher = download,
) -> dict[str, str]:
    """The files config says upstream declares in, out of this release.

    Read rather than merely named, for two reasons. A path that has stopped
    being in the archive is a config entry pointing at where the declaration
    used to be, and reporting the old path forever is the one failure worse
    than reporting nothing. And the text is what makes a version bump
    answerable: comparing it against the release the recipe is moving from
    says which of these files moved, which needs no vocabulary at all.

    **Every source, not one.** The readers that reconcile take one archive
    because joining two source trees into one declaration is meaningless, but
    this one only shows files -- and `tzcode` builds `tzcode<version>.tar.gz`
    beside `tzdata<version>.tar.gz`, with the Makefile in the first. Refusing
    a recipe for having two sources would refuse it for a shape that has no
    bearing on the question. The first source carrying a path answers it,
    which is the order `upstream_location` already reports a several-source
    recipe by.
    """
    found: dict[str, str] = {}
    urls: list[str] = []
    for source in archive_sources(recipe, config.feedstock):
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
    if missing:
        raise ForgeError(
            f"{', '.join(urls)}: has no {', '.join(missing)}\n"
            "  `upstream.declares` names the files upstream states its "
            "dependencies in, relative to the archive's top-level directory\n"
            "  a path that has stopped being there means the declaration has "
            "moved, and is worth finding rather than dropping"
        )
    # In the order config named them, which is the order a reader opens them,
    # rather than the order the sources happened to yield them.
    texts = {path: found[path] for path in upstream.declares}
    return texts


def moved_declarations(
    current: Mapping[str, str], previous: Mapping[str, str]
) -> tuple[str, ...]:
    """Which declared files differ between two releases, in config's order.

    A file this release added counts as moved -- upstream putting a new file
    where the declaration lives is exactly the thing worth looking at. One
    that has gone is not reported here, because `read_declaration` refuses the
    release outright rather than letting a stale path go quiet.
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
    """Both halves of a reader's join, keyed by the path each one has upstream.

    The same two reads the reader itself makes, and at the same `ref`, so the
    workbench cannot quote a build script from a different commit than the one
    that was reconciled against.

    Failures here are not raised. This runs after the plan the workbench is
    built around, so an archive that has since gone missing would turn a
    findings report into a traceback, and the findings are the thing being
    asked for. What is readable is shown and what is not is left out.
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
        parsed = parse_pyproject(text, f"{upstream.repo}/{path}@{tag}")
    except UpstreamError as exc:
        raise ForgeError(str(exc)) from exc
    # The path in the monorepo, which is the whole answer here: `source`
    # already carries the repo and the tag, and a provider's `pyproject.toml`
    # is one of a hundred-odd in that tree.
    return replace(parsed, declared_in=path)


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
