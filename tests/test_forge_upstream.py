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

from swage.config import load_config
from swage.forge import ForgeError, GitHub, fetch_upstream, sole_source
from swage.recipe import read_recipe

from .conftest import CONFIG_ROOT, REPO_ROOT

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


def test_several_sources_stop_the_feedstock_and_name_them() -> None:
    """`airflow-feedstock` builds one package from three sdists (DESIGN.md 3.3)."""
    recipe = read_recipe(
        "context:\n"
        '  version: "3.3.0"\n'
        "source:\n"
        "  - url: https://x.invalid/apache_airflow-${{ version }}.tar.gz\n"
        "    sha256: aa\n"
        "    target_directory: airflow\n"
        "  - url: https://x.invalid/apache_airflow_core-${{ version }}.tar.gz\n"
        "    sha256: bb\n"
        "    target_directory: airflow-core\n"
        "requirements:\n  run:\n    - python\n"
    )
    with pytest.raises(ForgeError, match="builds from 2 sources") as caught:
        sole_source(recipe, "airflow")
    # Naming them is the point: the report has to say which is which.
    assert "airflow-core" in str(caught.value)


def test_a_recipe_with_no_source_says_so() -> None:
    recipe = read_recipe("requirements:\n  run:\n    - python\n")
    with pytest.raises(ForgeError, match="declares no source"):
        sole_source(recipe, "demo")


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
