"""Tests for fetching one feedstock's upstream metadata (DESIGN.md 3.6).

These run against the repo's real `config/`, because a harness more permissive
than reality hides bugs as readily as it invents them: the airflow family's
tag and metadata templates are the thing under test, and a fixture rewriting
them would be testing the fixture.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from collections.abc import Sequence

import pytest

from swage.config import FeedstockConfig, ManualUpstream, load_config
from swage.forge import (
    ForgeError,
    GitHub,
    archive_sources,
    fetch_upstream,
    fetch_upstream_texts,
    moved_declarations,
    read_declaration,
)
from swage.forge.archive import Fetcher
from swage.recipe import Recipe, read_recipe
from swage.upstream import NothingToReconcile

from .conftest import CONFIG_ROOT, REPO_ROOT, WriteTree

CORPUS = REPO_ROOT / "tests" / "corpus"
TREE = load_config(CONFIG_ROOT)

#: The real google-cloud URL template, split only to stay inside the line
#: length -- the point of the test is that swage resolves this exact form.
PYPI_URL = (
    "https://pypi.org/packages/source/${{ name[0] }}/${{ name }}/"
    "${{ name|replace('-', '_') }}-${{ version }}.tar.gz"
)

PYPI_RECIPE = f"""\
context:
  name: google-cloud-bigquery
  version: "3.43.0"
source:
  url: {PYPI_URL}
  sha256: PLACEHOLDER
requirements:
  run:
    - python
"""

TAG_RECIPE = """\
context:
  name: apache-airflow-providers-apache-hive
  version: "9.6.1"
requirements:
  run:
    - python
"""


def make_sdist(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, text in files.items():
            payload = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


SDIST = make_sdist(
    {
        "google_cloud_bigquery-3.43.0/pyproject.toml": (
            CORPUS / "google-cloud" / "google-cloud-bigquery" / "pyproject.toml"
        ).read_text(encoding="utf-8")
    }
)


class FakeGitHub(GitHub):
    """Answers `file()` from a mapping, recording what was asked for."""

    def __init__(self, text: str) -> None:
        super().__init__(run=lambda argv: "")
        self.text = text
        self.asked: list[tuple[str, str, str]] = []

    def file(self, repo: str, path: str, ref: str) -> str:
        self.asked.append((repo, path, ref))
        return self.text


def test_the_airflow_family_builds_its_tag_and_path_from_the_feedstock_name() -> None:
    """`{slug}` and `{slug_path}` are what make one rule cover 99 feedstocks."""
    config = TREE.for_feedstock("apache-airflow-providers-apache-hive")
    provider = CORPUS / "airflow-providers" / "providers-apache-hive_9.6.1"
    github = FakeGitHub((provider / "pyproject.toml").read_text(encoding="utf-8"))
    metadata = fetch_upstream(read_recipe(TAG_RECIPE), config, github=github)
    assert github.asked == [
        (
            "apache/airflow",
            "providers/apache/hive/pyproject.toml",
            "providers-apache-hive/9.6.1",
        )
    ]
    assert metadata.name == "apache-airflow-providers-apache-hive"
    assert metadata.version == "9.6.1"
    # The repo and tag are already in the report's `source`; what is left to
    # answer is which of the monorepo's hundred-odd pyprojects this was.
    assert metadata.declared_in == "providers/apache/hive/pyproject.toml"


def test_the_slug_is_whatever_the_family_glob_matched() -> None:
    for feedstock, slug in [
        ("apache-airflow-providers-apache-hive", "apache-hive"),
        ("apache-airflow-providers-amazon", "amazon"),
        ("google-cloud-bigquery", "bigquery"),
        # No family, so there is no prefix to strip.
        ("smmap", "smmap"),
    ]:
        assert TREE.for_feedstock(feedstock).slug == slug


def test_a_tag_recipe_with_no_version_says_what_is_missing() -> None:
    config = TREE.for_feedstock("apache-airflow-providers-apache-hive")
    recipe = read_recipe("requirements:\n  run:\n    - python\n")
    with pytest.raises(ForgeError, match="no version"):
        fetch_upstream(recipe, config, github=FakeGitHub(""))


def test_a_feedstock_that_packages_no_distribution_is_not_read_at_all(
    write_tree: WriteTree,
) -> None:
    """The archive is never fetched, which is the whole point of the entry.

    The E3SM archive carries `pyscream`'s `pyproject.toml`, so reading it
    succeeds and describes a different package. Refusing after the fetch would
    still leave that metadata in hand; refusing before it means there is
    nothing to reconcile against by construction.
    """
    root = write_tree(
        {
            "defaults.yaml": "trust: never\nrecipe_owned:\n  names: [python]\n",
            "feedstocks/demo.yaml": (
                "feedstock: demo\n"
                "upstream:\n"
                "  source: none\n"
                "  reason: Fortran binaries and scripts, whose deps are "
                "their imports\n"
            ),
        }
    )
    digest = hashlib.sha256(SDIST).hexdigest()
    recipe = read_recipe(PYPI_RECIPE.replace("PLACEHOLDER", digest))

    def explode(url: str) -> bytes:
        raise AssertionError(f"fetched {url}")

    with pytest.raises(NothingToReconcile) as caught:
        fetch_upstream(recipe, load_config(root).for_feedstock("demo"), fetch=explode)

    assert "demo packages no python distribution" in str(caught.value)
    assert "their imports" in str(caught.value)


def test_the_pypi_path_reads_the_archive_the_recipe_pins() -> None:
    digest = hashlib.sha256(SDIST).hexdigest()
    recipe = read_recipe(PYPI_RECIPE.replace("PLACEHOLDER", digest))
    fetched: list[str] = []

    def fetch(url: str) -> bytes:
        fetched.append(url)
        return SDIST

    metadata = fetch_upstream(
        recipe, TREE.for_feedstock("google-cloud-bigquery"), fetch=fetch
    )
    assert fetched == [
        "https://pypi.org/packages/source/g/google-cloud-bigquery/"
        "google_cloud_bigquery-3.43.0.tar.gz"
    ]
    assert metadata.name == "google-cloud-bigquery"


def test_a_feedstock_with_no_upstream_configured_reads_its_archive() -> None:
    """The recipe already says where to look, so this needs no config entry."""
    digest = hashlib.sha256(SDIST).hexdigest()
    recipe = read_recipe(PYPI_RECIPE.replace("PLACEHOLDER", digest))
    config = TREE.for_feedstock("some-feedstock-with-no-file")
    assert config.upstream is None
    assert fetch_upstream(recipe, config, fetch=lambda _: SDIST).name == (
        "google-cloud-bigquery"
    )


def test_a_recipe_with_no_source_says_so() -> None:
    recipe = read_recipe("requirements:\n  run:\n    - python\n")
    with pytest.raises(ForgeError, match="declares no source"):
        archive_sources(recipe, "demo")


def test_a_source_with_no_hash_is_refused_by_name() -> None:
    """There is nothing to verify what was read against (DESIGN.md 3.6)."""
    recipe = read_recipe(
        "source:\n"
        "  - url: https://x.invalid/a.tar.gz\n"
        "    sha256: aa\n"
        "    target_directory: one\n"
        "  - url: https://x.invalid/b.tar.gz\n"
        "    target_directory: two\n"
        "requirements:\n  run:\n    - python\n"
    )
    with pytest.raises(ForgeError, match="two is not a URL with a sha256"):
        archive_sources(recipe, "demo")


# --- a recipe that builds several releases (DESIGN.md 3.6) -------------------
#
# Three sdists at two versions, which is `airflow-feedstock`'s shape reduced to
# what the resolver reads: the outputs, and the project each archive declares.

SPLIT_RECIPE = """\
context:
  version: "3.3.1"
source:
  - url: https://x.invalid/apache_airflow-${{ version }}.tar.gz
    sha256: SHA_MAIN
    target_directory: airflow
  - url: https://x.invalid/apache_airflow_core-${{ version }}.tar.gz
    sha256: SHA_CORE
    target_directory: airflow-core
outputs:
  - package:
      name: apache-airflow-core
    requirements:
      run:
        - python
  - package:
      name: apache-airflow
    requirements:
      run:
        - python
  - package:
      name: apache-airflow-core-with-all
    requirements:
      run:
        - python
"""


#: One archive per project, each declaring its own name and dependencies.
#:
#: Built once, because `make_sdist` writes a gzip stream and gzip records
#: the time it was written: two calls a second apart produce different bytes
#: and the second no longer matches the sha256 the recipe was built with.
#: That is a real check doing its job -- swage refuses an archive whose
#: bytes are not the ones the recipe claims -- on a fixture that lied to it.
SPLIT_SDISTS: dict[str, bytes] = {
    "apache-airflow": make_sdist(
        {
            "apache_airflow-3.3.1/pyproject.toml": (
                "[project]\n"
                'name = "apache-airflow"\n'
                'version = "3.3.1"\n'
                'dependencies = ["apache-airflow-core==3.3.1"]\n'
                "[project.optional-dependencies]\n"
                'amazon = ["apache-airflow-providers-amazon>=9.0.0"]\n'
            )
        }
    ),
    "apache-airflow-core": make_sdist(
        {
            "apache_airflow_core-3.3.1/pyproject.toml": (
                "[project]\n"
                'name = "apache-airflow-core"\n'
                'version = "3.3.1"\n'
                'dependencies = ["alembic>=1.13.1"]\n'
                "[project.optional-dependencies]\n"
                'statsd = ["statsd>=3.3.0"]\n'
            )
        }
    ),
}


def split_recipe() -> str:
    text = SPLIT_RECIPE
    for placeholder, project in (
        ("SHA_MAIN", "apache-airflow"),
        ("SHA_CORE", "apache-airflow-core"),
    ):
        text = text.replace(
            placeholder, hashlib.sha256(SPLIT_SDISTS[project]).hexdigest()
        )
    return text


def serve_split() -> Fetcher:
    archives = SPLIT_SDISTS

    def fetch(url: str) -> bytes:
        if "apache_airflow_core" in url:
            return archives["apache-airflow-core"]
        return archives["apache-airflow"]

    return fetch


#: Enough of a quirks database for `load_config` to resolve against; the
#: feedstock file below is the part under test.
DEFAULTS = """\
trust: never
recipe_owned:
  names: [python, pip]
"""


def split_config(write_tree: WriteTree, feedstock_file: str) -> FeedstockConfig:
    root = write_tree(
        {"defaults.yaml": DEFAULTS, "feedstocks/airflow.yaml": feedstock_file}
    )
    return load_config(root).for_feedstock("airflow")


SPLIT_CONFIG = """\
feedstock: airflow
outputs:
  apache-airflow-core:
    run:
      core: true
  apache-airflow-core-with-all:
    upstream: apache-airflow-core
    run:
      core: false
      extras:
        - statsd
"""


def test_an_output_draws_on_the_release_that_declares_its_name(
    write_tree: WriteTree,
) -> None:
    """The archive says which output it is, so nothing has to be written down."""
    upstream = fetch_upstream(
        read_recipe(split_recipe()),
        split_config(write_tree, SPLIT_CONFIG),
        fetch=serve_split(),
    )
    assert [release.name for release in upstream.releases] == [
        "apache-airflow",
        "apache-airflow-core",
    ]
    # The first source is what the feedstock is a release *of*, and what the
    # report and the commit message name.
    assert upstream.primary.name == "apache-airflow"
    assert upstream.for_output("apache-airflow").name == "apache-airflow"
    assert upstream.for_output("apache-airflow-core").name == "apache-airflow-core"


def test_a_metapackage_is_placed_by_config(write_tree: WriteTree) -> None:
    """No archive declares `-with-all`, so `outputs[].upstream` says which."""
    upstream = fetch_upstream(
        read_recipe(split_recipe()),
        split_config(write_tree, SPLIT_CONFIG),
        fetch=serve_split(),
    )
    assert (
        upstream.for_output("apache-airflow-core-with-all").name
        == "apache-airflow-core"
    )


def test_an_output_nothing_places_stops_the_feedstock(write_tree: WriteTree) -> None:
    """Reconciling it against whichever came first is the wrong answer."""
    config = split_config(write_tree, "feedstock: airflow\n")
    with pytest.raises(ForgeError, match="apache-airflow-core-with-all") as caught:
        fetch_upstream(read_recipe(split_recipe()), config, fetch=serve_split())
    message = str(caught.value)
    # Both halves of the remedy: what there is to choose from, and the key.
    assert "apache-airflow, apache-airflow-core" in message
    assert "outputs.<output>.upstream" in message


def test_a_config_entry_naming_no_source_says_that_rather_than_nothing(
    write_tree: WriteTree,
) -> None:
    """Pointing at the key the maintainer already wrote is the wrong advice."""
    config = split_config(
        write_tree,
        "feedstock: airflow\n"
        "outputs:\n"
        "  apache-airflow-core-with-all:\n"
        "    upstream: apache-airflow-cor\n"
        "    run: {core: false}\n",
    )
    with pytest.raises(ForgeError, match="which none of the recipe's") as caught:
        fetch_upstream(read_recipe(split_recipe()), config, fetch=serve_split())
    assert "apache-airflow-core-with-all names apache-airflow-cor" in str(caught.value)


#: One release, pinned twice: the sdist the package is built from and a GitHub
#: archive of the same tag, whose tree the tests need. `authlib` and `aiohttp`
#: both do it.
TWICE_PINNED = make_sdist(
    {
        "aiohttp-3.13.4/pyproject.toml": (
            "[project]\n"
            'name = "aiohttp"\n'
            'version = "3.13.4"\n'
            # Stated so the sdist is not treated as one whose dependency list
            # has to be recovered from the wheel (DESIGN.md 3.6.2).
            'dependencies = ["multidict>=4.5"]\n'
        )
    }
)


def _twice_pinned_recipe(outputs: str = "") -> Recipe:
    digest = hashlib.sha256(TWICE_PINNED).hexdigest()
    return read_recipe(
        "source:\n"
        f"  - url: https://x.invalid/aiohttp-3.13.4.tar.gz\n    sha256: {digest}\n"
        f"  - url: https://x.invalid/v3.13.4.tar.gz\n    sha256: {digest}\n"
        "    target_directory: sources\n"
        + (
            outputs
            or "package:\n  name: aiohttp\n  version: 3.13.4\n"
            "requirements:\n  run:\n    - python\n"
        )
    )


def test_sources_that_do_not_tell_two_outputs_apart_stop_the_feedstock(
    write_tree: WriteTree,
) -> None:
    """The name that tells releases apart does not tell these apart.

    Two outputs and two indistinguishable releases is the case with nothing
    left to decide with: `outputs[].upstream` names a project, and both
    sources answer to the same one.
    """
    recipe = _twice_pinned_recipe(
        "outputs:\n"
        "  - package:\n      name: aiohttp\n"
        "    requirements:\n      run:\n        - python\n"
        "  - package:\n      name: aiohttp-with-speedups\n"
        "    requirements:\n      run:\n        - python\n"
    )
    config = split_config(write_tree, "feedstock: airflow\n")
    with pytest.raises(ForgeError, match="declare the same project, aiohttp"):
        fetch_upstream(recipe, config, fetch=lambda _: TWICE_PINNED)


def test_one_output_takes_the_source_the_recipe_builds(
    write_tree: WriteTree,
) -> None:
    """With one output the ambiguity is not one, and refusing was a bug.

    Whichever archive is chosen, that output reconciles against it and the two
    are the same release. `authlib` is a single noarch package with its test
    tree unpacked beside it, and it was stopped over a question about telling
    outputs apart that it does not have.
    """
    upstream = fetch_upstream(
        _twice_pinned_recipe(),
        split_config(write_tree, "feedstock: airflow\n"),
        fetch=lambda _: TWICE_PINNED,
    )

    assert upstream.primary.name == "aiohttp"
    assert [item.name for item in upstream.primary.dependencies] == ["multidict"]


def test_the_workbench_names_each_source_by_where_it_unpacks(
    write_tree: WriteTree,
) -> None:
    """Three archives ship three `pyproject.toml`, and one would hide the rest."""
    texts = fetch_upstream_texts(
        read_recipe(split_recipe()),
        split_config(write_tree, SPLIT_CONFIG),
        fetch=serve_split(),
    )
    assert sorted(texts) == ["airflow-core/pyproject.toml", "airflow/pyproject.toml"]
    assert 'name = "apache-airflow-core"' in texts["airflow-core/pyproject.toml"]


def test_one_source_still_names_its_file_plainly(write_tree: WriteTree) -> None:
    """The prefix is for telling several apart, so it is absent where there is one."""
    digest = hashlib.sha256(SDIST).hexdigest()
    recipe = read_recipe(PYPI_RECIPE.replace("PLACEHOLDER", digest))
    texts = fetch_upstream_texts(
        recipe, TREE.for_feedstock("google-cloud-bigquery"), fetch=lambda _: SDIST
    )
    assert sorted(texts) == ["pyproject.toml"]


def test_nothing_reaches_the_network_by_accident() -> None:
    """Every fetch goes through an injected seam, so a test cannot leak out."""
    recipe = read_recipe(PYPI_RECIPE.replace("PLACEHOLDER", "deadbeef" * 8))

    def refuse(url: str) -> bytes:
        raise AssertionError(f"unexpected download: {url}")

    with pytest.raises(AssertionError, match="unexpected download"):
        fetch_upstream(
            recipe, TREE.for_feedstock("google-cloud-bigquery"), fetch=refuse
        )


def test_the_gh_runner_is_never_invoked_by_the_pypi_path() -> None:
    def refuse(argv: Sequence[str]) -> str:
        raise AssertionError(f"unexpected gh call: {list(argv)}")

    digest = hashlib.sha256(SDIST).hexdigest()
    recipe = read_recipe(PYPI_RECIPE.replace("PLACEHOLDER", digest))
    metadata = fetch_upstream(
        recipe,
        TREE.for_feedstock("google-cloud-bigquery"),
        github=GitHub(run=refuse),
        fetch=lambda _: SDIST,
    )
    assert metadata.name == "google-cloud-bigquery"


# --- the workbench for a feedstock with a reader ----------------------------


CMAKE_ARCHIVE = make_sdist(
    {
        "proj-9.8.1/CMakeLists.txt": "find_package(SQLite3 REQUIRED)\n",
        "proj-9.8.1/README.md": "not the declaration\n",
    }
)

CMAKE_RECIPE = """\
context:
  name: proj
  version: "9.8.1"
source:
  url: https://x.invalid/proj-9.8.1.tar.gz
  sha256: PLACEHOLDER
requirements:
  host:
    - libsqlite
"""


def _cmake_config(write_tree: WriteTree) -> FeedstockConfig:
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            "feedstocks/proj.4.yaml": "feedstock: proj.4\nupstream:\n  source: cmake\n",
        }
    )
    return load_config(root).for_feedstock("proj.4")


def test_the_workbench_shows_a_reader_the_files_its_reader_read(
    write_tree: WriteTree,
) -> None:
    """The whole value of a reader is saying where upstream states its needs.

    Falling through to the metadata search showed `proj.4` nothing at all --
    its archive has no `pyproject.toml` and no `PKG-INFO` -- so the workbench
    was empty on exactly the feedstock whose declaration is hardest to find.
    """
    digest = hashlib.sha256(CMAKE_ARCHIVE).hexdigest()
    texts = fetch_upstream_texts(
        read_recipe(CMAKE_RECIPE.replace("PLACEHOLDER", digest)),
        _cmake_config(write_tree),
        github=FakeGitHub("cmake -D EXE_SQLITE3=x\n"),
        fetch=lambda _: CMAKE_ARCHIVE,
    )
    assert sorted(texts) == ["CMakeLists.txt", "recipe/build.sh"]
    assert "find_package(SQLite3 REQUIRED)" in texts["CMakeLists.txt"]


def test_the_workbench_reads_the_build_script_at_the_commit_it_was_asked_for(
    write_tree: WriteTree,
) -> None:
    """Quoting a build script from another commit answers about another build."""
    digest = hashlib.sha256(CMAKE_ARCHIVE).hexdigest()
    github = FakeGitHub("cmake\n")
    fetch_upstream_texts(
        read_recipe(CMAKE_RECIPE.replace("PLACEHOLDER", digest)),
        _cmake_config(write_tree),
        github=github,
        fetch=lambda _: CMAKE_ARCHIVE,
        ref="4a2f1c8",
    )
    assert github.asked == [
        ("conda-forge/proj.4-feedstock", "recipe/build.sh", "4a2f1c8")
    ]


def test_a_workbench_survives_a_build_script_it_cannot_read(
    write_tree: WriteTree,
) -> None:
    """Findings are what was asked for, so half the join beats a traceback."""

    class Refusing(FakeGitHub):
        def file(self, repo: str, path: str, ref: str) -> str:
            raise ForgeError("no such file")

    digest = hashlib.sha256(CMAKE_ARCHIVE).hexdigest()
    texts = fetch_upstream_texts(
        read_recipe(CMAKE_RECIPE.replace("PLACEHOLDER", digest)),
        _cmake_config(write_tree),
        github=Refusing(""),
        fetch=lambda _: CMAKE_ARCHIVE,
    )
    assert sorted(texts) == ["CMakeLists.txt"]


def test_a_reader_reads_whichever_release_it_is_handed() -> None:
    """The second fetch DESIGN.md 3.3.7 costs works for a reader unchanged.

    `_previous_upstream` hands `fetch_upstream` the recipe off the branch the
    pull request targets, and the dispatch is on config rather than on which
    release it is -- so the previous version's `CMakeLists.txt` is read out of
    the previous version's archive with no reader-specific plumbing. Pinned
    because DESIGN.md 3.6.6 said the opposite, and because no reader-backed
    feedstock has yet had an open bot pull request to prove it either way.
    """
    old = make_sdist({"proj-9.8.0/CMakeLists.txt": "find_package(SQLite3 REQUIRED)\n"})
    new = make_sdist(
        {
            "proj-9.8.1/CMakeLists.txt": (
                "find_package(SQLite3 REQUIRED)\nfind_package(TIFF REQUIRED)\n"
            )
        }
    )
    # The fleet's own `proj.4` config, so the name table is the real one.
    config = TREE.for_feedstock("proj.4")
    seen = {}
    for version, payload in (("9.8.0", old), ("9.8.1", new)):
        recipe = read_recipe(
            CMAKE_RECIPE.replace(
                "PLACEHOLDER", hashlib.sha256(payload).hexdigest()
            ).replace("9.8.1", version)
        )

        def serve(url: str, body: bytes = payload) -> bytes:
            return body

        metadata = fetch_upstream(
            recipe, config, github=FakeGitHub("cmake\n"), fetch=serve
        )
        seen[version] = [r.name for r in metadata.primary.build_requires or ()]
    assert seen == {"9.8.0": ["libsqlite"], "9.8.1": ["libsqlite", "libtiff"]}


# --- the files a feedstock swage does not read declares in ------------------


MANUAL_ARCHIVE = make_sdist(
    {
        "demo-1.0/configure.ac": "ACX_NETCDF\n",
        "demo-1.0/m4/netcdf.m4": "AC_DEFUN([ACX_NETCDF], [])\n",
        "demo-1.0/README": "not the declaration\n",
    }
)

MANUAL_RECIPE = """\
context:
  name: demo
  version: "1.0"
source:
  url: https://x.invalid/demo-1.0.tar.gz
  sha256: PLACEHOLDER
requirements:
  host:
    - libnetcdf
"""


def _manual_config(write_tree: WriteTree, declares: str) -> FeedstockConfig:
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            "feedstocks/demo.yaml": (
                "feedstock: demo\nupstream:\n  source: manual\n"
                f"  declares:\n{declares}"
                "  reason: demo declares through an m4 macro of its own.\n"
            ),
        }
    )
    return load_config(root).for_feedstock("demo")


def _manual_upstream(config: FeedstockConfig) -> ManualUpstream:
    upstream = config.upstream
    assert isinstance(upstream, ManualUpstream)
    return upstream


def _manual_recipe() -> Recipe:
    digest = hashlib.sha256(MANUAL_ARCHIVE).hexdigest()
    return read_recipe(MANUAL_RECIPE.replace("PLACEHOLDER", digest))


def test_the_declared_files_are_read_at_their_own_paths(
    write_tree: WriteTree,
) -> None:
    """Named, and then read: the paths are what a maintainer opens."""
    config = _manual_config(write_tree, "    - configure.ac\n    - m4/netcdf.m4\n")
    texts = read_declaration(
        _manual_recipe(),
        config,
        _manual_upstream(config),
        fetch=lambda _: MANUAL_ARCHIVE,
    )
    assert sorted(texts) == ["configure.ac", "m4/netcdf.m4"]
    assert "AC_DEFUN([ACX_NETCDF]" in texts["m4/netcdf.m4"]


def test_a_declared_path_that_is_not_in_the_archive_stops_the_feedstock(
    write_tree: WriteTree,
) -> None:
    """Upstream moving the declaration is the one thing that can go wrong here.

    Pointing a maintainer at a file deleted two releases ago is worse than
    saying nothing, and it would never correct itself: nothing else in swage
    looks at these paths.
    """
    config = _manual_config(write_tree, "    - configure.ac\n    - m4/gone.m4\n")
    with pytest.raises(ForgeError, match=r"has no m4/gone\.m4"):
        read_declaration(
            _manual_recipe(),
            config,
            _manual_upstream(config),
            fetch=lambda _: MANUAL_ARCHIVE,
        )


#: `r-proj4`'s shape: a list of CRAN mirrors built from a `cran_mirror`
#: conda-build supplies and swage cannot resolve, so there is no URL to fetch.
UNFETCHABLE_RECIPE = """\
context:
  name: demo
  version: "1.0"
source:
  url:
    - ${{ cran_mirror }}/src/contrib/demo_1.0.tar.gz
    - ${{ cran_mirror }}/src/contrib/Archive/demo/demo_1.0.tar.gz
  sha256: 0000000000000000000000000000000000000000000000000000000000000000
requirements:
  host:
    - proj
"""


def test_a_recipe_with_no_fetchable_archive_returns_no_files(
    write_tree: WriteTree,
) -> None:
    """Nothing to check the paths against, so nothing is checked or refused.

    The check exists to catch a path that has *stopped* being in the archive.
    With no archive there is nothing saying it has, and refusing the feedstock
    would trade a pointer somebody can use for a failure nobody can act on.
    """
    config = _manual_config(write_tree, "    - DESCRIPTION\n    - configure.ac\n")

    def refuse(url: str) -> bytes:
        raise AssertionError(f"nothing should be fetched, and {url} was")

    assert (
        read_declaration(
            read_recipe(UNFETCHABLE_RECIPE),
            config,
            _manual_upstream(config),
            fetch=refuse,
        )
        == {}
    )


def test_only_the_files_that_differ_are_named_as_moved() -> None:
    current = {"configure.ac": "same\n", "m4/netcdf.m4": "new\n"}
    previous = {"configure.ac": "same\n", "m4/netcdf.m4": "old\n"}
    assert moved_declarations(current, previous) == ("m4/netcdf.m4",)
    assert moved_declarations(current, current) == ()


def test_a_file_this_release_added_counts_as_moved() -> None:
    """Upstream putting the declaration somewhere new is the case to catch."""
    current = {"configure.ac": "same\n", "m4/netcdf.m4": "new\n"}
    assert moved_declarations(current, {"configure.ac": "same\n"}) == ("m4/netcdf.m4",)


def test_the_moved_files_keep_the_order_config_named_them_in() -> None:
    """Config orders these as a reader should open them, so the report does too."""
    current = {"configure.ac": "a\n", "m4/netcdf.m4": "b\n", "m4/png.m4": "c\n"}
    assert moved_declarations(current, {}) == (
        "configure.ac",
        "m4/netcdf.m4",
        "m4/png.m4",
    )


MANUAL_SECOND = make_sdist({"data-1.0/zones.tab": "# the other archive\n"})

MANUAL_TWO_SOURCES = """\
context:
  name: demo
  version: "1.0"
source:
  - url: https://x.invalid/demo-1.0.tar.gz
    sha256: FIRST
  - url: https://x.invalid/data-1.0.tar.gz
    sha256: SECOND
    target_directory: data
requirements:
  host:
    - libnetcdf
"""


def test_a_declaration_is_found_in_whichever_source_carries_it(
    write_tree: WriteTree,
) -> None:
    """`tzcode` builds two archives and its Makefile is in the first.

    A reader that reconciles takes one archive, because joining two source
    trees into one declaration is meaningless. This one only shows files, so
    refusing the recipe for having two sources would refuse it over a shape
    with no bearing on the question.
    """
    config = _manual_config(write_tree, "    - configure.ac\n    - zones.tab\n")
    recipe = read_recipe(
        MANUAL_TWO_SOURCES.replace(
            "FIRST", hashlib.sha256(MANUAL_ARCHIVE).hexdigest()
        ).replace("SECOND", hashlib.sha256(MANUAL_SECOND).hexdigest())
    )
    by_url = {
        "https://x.invalid/demo-1.0.tar.gz": MANUAL_ARCHIVE,
        "https://x.invalid/data-1.0.tar.gz": MANUAL_SECOND,
    }
    texts = read_declaration(
        recipe, config, _manual_upstream(config), fetch=lambda url: by_url[url]
    )
    assert list(texts) == ["configure.ac", "zones.tab"]
    assert texts["zones.tab"] == "# the other archive\n"


def test_a_path_in_none_of_the_sources_names_every_url_it_looked_in(
    write_tree: WriteTree,
) -> None:
    config = _manual_config(write_tree, "    - configure.ac\n    - m4/gone.m4\n")
    recipe = read_recipe(
        MANUAL_TWO_SOURCES.replace(
            "FIRST", hashlib.sha256(MANUAL_ARCHIVE).hexdigest()
        ).replace("SECOND", hashlib.sha256(MANUAL_SECOND).hexdigest())
    )
    by_url = {
        "https://x.invalid/demo-1.0.tar.gz": MANUAL_ARCHIVE,
        "https://x.invalid/data-1.0.tar.gz": MANUAL_SECOND,
    }
    with pytest.raises(ForgeError, match=r"has no m4/gone\.m4"):
        read_declaration(
            recipe, config, _manual_upstream(config), fetch=lambda url: by_url[url]
        )
