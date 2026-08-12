"""Tests for reading upstream metadata out of a source archive (DESIGN.md 3.6).

The archives are built here rather than downloaded, so there is no network in
the tests. Their *contents* are the real `google-cloud-bigquery` 3.43.0 files
from the corpus, which is what makes "prefer pyproject.toml" a claim about
metadata that genuinely disagrees rather than about two fixtures written to.
"""

from __future__ import annotations

import hashlib
import io
import tarfile

import pytest

from swage.forge import ForgeError, parse_archive, read_archive

from .conftest import REPO_ROOT

BIGQUERY = REPO_ROOT / "tests" / "corpus" / "google-cloud" / "google-cloud-bigquery"
PKG_INFO = (BIGQUERY / "PKG-INFO").read_text(encoding="utf-8")
PYPROJECT = (BIGQUERY / "pyproject.toml").read_text(encoding="utf-8")


def make_sdist(files: dict[str, str]) -> bytes:
    """A tar.gz laid out the way an sdist is: one top-level directory."""
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
        "google_cloud_bigquery-3.43.0/pyproject.toml": PYPROJECT,
        "google_cloud_bigquery-3.43.0/PKG-INFO": PKG_INFO,
    }
)


def test_pyproject_wins_over_pkg_info_because_only_it_has_build_system() -> None:
    """`host` cannot be reconciled from core metadata alone (DESIGN.md 3.6.2)."""
    metadata = parse_archive(SDIST, "sdist")
    assert metadata.name == "google-cloud-bigquery"
    assert metadata.build_requires is not None
    assert [r.name for r in metadata.build_requires] == ["setuptools"]


def test_pkg_info_is_read_where_the_archive_has_no_pyproject() -> None:
    archive = make_sdist({"google_cloud_bigquery-3.43.0/PKG-INFO": PKG_INFO})
    metadata = parse_archive(archive, "sdist")
    assert metadata.name == "google-cloud-bigquery"
    # Told nothing about building, as opposed to told there is nothing to do.
    assert metadata.build_requires is None


def test_the_shallowest_pyproject_wins() -> None:
    """A vendored copy deeper in the tree is not the project's own metadata."""
    archive = make_sdist(
        {
            "demo-1.0/docs/vendor/pyproject.toml": "[project]\nname = 'vendored'\n",
            "demo-1.0/pyproject.toml": "[project]\nname = 'demo'\n",
        }
    )
    assert parse_archive(archive, "sdist").name == "demo"


def test_an_archive_with_no_metadata_at_all_says_so() -> None:
    archive = make_sdist({"demo-1.0/README.md": "hello\n"})
    with pytest.raises(ForgeError, match=r"neither a pyproject\.toml nor a PKG-INFO"):
        parse_archive(archive, "sdist")


def test_something_that_is_not_a_tarball_is_named_as_such() -> None:
    with pytest.raises(ForgeError, match="cannot read as a tar archive"):
        parse_archive(b"PK\x03\x04 this is a zip", "sdist")


def test_metadata_swage_cannot_read_keeps_its_own_message() -> None:
    """The upstream layer's refusals are the useful ones; do not paper over them."""
    dynamic = '[project]\nname = "demo"\ndynamic = ["dependencies"]\n'
    archive = make_sdist({"demo-1.0/pyproject.toml": dynamic})
    with pytest.raises(ForgeError, match="dependencies as dynamic"):
        parse_archive(archive, "sdist")


# A poetry project, which declares no PEP 621 `[project]` table at all. 15 of
# the fleet's 88 archives are this shape and 3 more compute their dependencies
# at build time -- together a fifth of it, every one an sdist whose PKG-INFO
# states the same dependencies outright.
POETRY = """\
[build-system]
requires = ["poetry-core>=1.0.0"]
build-backend = "poetry.core.masonry.api"

[tool.poetry]
name = "demo"
version = "1.0"
"""

PKG_INFO_DEMO = """\
Metadata-Version: 2.1
Name: demo
Version: 1.0
Requires-Dist: sqlalchemy>=2.0
"""


@pytest.mark.parametrize(
    ("pyproject", "why"),
    [
        (POETRY, "no [project] table"),
        (
            '[build-system]\nrequires = ["poetry-core>=1.0.0"]\n'
            '[project]\nname = "demo"\ndynamic = ["dependencies"]\n',
            "dependencies computed at build time",
        ),
    ],
)
def test_pkg_info_supplies_what_an_unreadable_pyproject_cannot(
    pyproject: str, why: str
) -> None:
    """Refusing here would strand a fifth of the fleet holding usable metadata."""
    archive = make_sdist(
        {"demo-1.0/pyproject.toml": pyproject, "demo-1.0/PKG-INFO": PKG_INFO_DEMO}
    )
    metadata = parse_archive(archive, why)
    assert [r.name for r in metadata.dependencies] == ["sqlalchemy"]
    # And `[build-system]` still comes from the file that has it, which is the
    # only thing a `host` section can be reconciled against (DESIGN.md 3.6.2).
    assert metadata.build_requires is not None
    assert [r.name for r in metadata.build_requires] == ["poetry-core"]


def test_an_unreadable_pyproject_with_no_pkg_info_beside_it_still_refuses() -> None:
    """The fallback is PKG-INFO, not a guess -- three fleet archives land here."""
    archive = make_sdist({"demo-1.0/pyproject.toml": POETRY})
    with pytest.raises(ForgeError, match=r"has no \[project\] table"):
        parse_archive(archive, "sdist")


def test_the_recipes_hash_is_verified_before_anything_is_read() -> None:
    digest = hashlib.sha256(SDIST).hexdigest()
    assert read_archive("https://x.invalid/s.tar.gz", digest, lambda _: SDIST).name == (
        "google-cloud-bigquery"
    )


def test_a_hash_mismatch_is_a_hard_failure() -> None:
    """Reconciling against the wrong release is worse than against nothing."""
    with pytest.raises(ForgeError, match="sha256 does not match the recipe"):
        read_archive("https://x.invalid/s.tar.gz", "0" * 64, lambda _: SDIST)
