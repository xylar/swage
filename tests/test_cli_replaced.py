"""Tests for pushing a replaced package (DESIGN.md §9.6).

The rename is made before planning, as a source-version correction is, so what
is worth pinning is the same: a run whose only change is the rename pushes it,
the change is measured from the recipe as read, the commit and the record say
why, and a `host` line, which the planner writes whole, comes out renamed.

The harness is `test_cli_source_versions`'s.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

import pytest

from swage.cli.pipeline import NameSources
from swage.cli.update import run_update
from swage.config import MappingLayer, load_config
from swage.forge import ForgeError, Git, GitHub
from swage.mapping import StaticPackageIndex

from .conftest import CONFIG_ROOT
from .test_cli_scan import FakeGitHub, pull
from .test_cli_source_versions import sdist
from .test_cli_update import NEW_SHA, FakeForge

SDIST = sdist("demo", "3.3.1", '"attrs>=22"')
URL = "https://example.invalid/demo-3.3.1.tar.gz"


def recipe_text(section: str, tool: str) -> str:
    build = f"  build:\n    - {tool}\n" if section == "build" else ""
    host = f"    - {tool}\n" if section == "host" else ""
    return f"""\
schema_version: 1

context:
  version: "3.3.1"
  python_min: '3.10'

package:
  name: demo
  version: ${{{{ version }}}}

source:
  url: {URL}
  sha256: {hashlib.sha256(SDIST).hexdigest()}

build:
  noarch: python

requirements:
{build}  host:
    - python ${{{{ python_min }}}}.*
    - pip
    - flit-core ==3.12.0
{host}  run:
    - python >=${{{{ python_min }}}}
    - attrs >=22
"""


def fetcher(url: str, timeout: float = 60.0) -> bytes:
    if url != URL:
        raise ForgeError(f"{url}: download failed: HTTP 404")
    return SDIST


@pytest.fixture
def names() -> NameSources:
    return NameSources(
        StaticPackageIndex.of("demo", "attrs", "flit-core", "pkgconf"),
        MappingLayer("grayskull pypi mapping", {}),
    )


def update(
    tmp_path: Path,
    names: NameSources,
    recipe: str,
    trust: str = "auto",
    also: str = "",
) -> tuple[FakeForge, Any]:
    root = tmp_path / "config"
    shutil.copytree(CONFIG_ROOT, root)
    (root / "feedstocks" / "demo.yaml").write_text(
        f"feedstock: demo\ntrust: {trust}\n{also}", encoding="utf-8"
    )
    forge = FakeForge(FakeGitHub(pulls=[pull()], files={"recipe/recipe.yaml": recipe}))
    run = run_update(
        GitHub(run=forge),
        Git(run=forge, root=tmp_path / "clones"),
        load_config(root),
        ["demo"],
        names,
        write=True,
        fetch=fetcher,
    )
    return forge, run.feedstocks[0]


def test_a_rename_alone_is_pushed_and_labeled(
    tmp_path: Path, names: NameSources
) -> None:
    forge, record = update(tmp_path, names, recipe_text("build", "pkg-config"))

    assert record.outcome == "automerge"
    assert record.reason == "+1 -1 in the recipe"
    assert record.pushed == NEW_SHA
    assert record.current_recipe == recipe_text("build", "pkg-config")
    assert record.rendered_recipe == recipe_text("build", "pkgconf")
    assert forge.order == ["clone", "commit", "push", "unlabel", "label", "comment"]


def test_the_commit_and_the_record_say_what_was_replaced(
    tmp_path: Path, names: NameSources
) -> None:
    forge, record = update(tmp_path, names, recipe_text("build", "pkg-config"))

    said = "pkg-config with pkgconf, which conda-forge asks for instead"
    (commit,) = forge.wrote("git", "commit")
    message = commit[commit.index("--message") + 1]
    assert f"Also replaces:\n  - {said}" in message
    assert f"replaced {said}" in record.notes


def test_a_host_line_is_planned_under_its_new_name(
    tmp_path: Path, names: NameSources
) -> None:
    _, record = update(
        tmp_path, names, recipe_text("host", "pkg-config"), trust="propose"
    )

    assert record.rendered_recipe == recipe_text("host", "pkgconf")


def test_a_recipe_already_on_pkgconf_is_unchanged(
    tmp_path: Path, names: NameSources
) -> None:
    forge, record = update(tmp_path, names, recipe_text("build", "pkgconf"))

    assert not forge.wrote("git", "commit")
    assert not any(note.startswith("replaced ") for note in record.notes)


def test_the_comment_says_the_rename_is_the_whole_change(
    tmp_path: Path, names: NameSources
) -> None:
    """Not "updated to match" a release whose metadata says nothing about
    pkgconf (DESIGN.md §9.8)."""
    forge, _ = update(tmp_path, names, recipe_text("build", "pkg-config"))

    (body,) = forge.comments
    assert body.startswith(
        "swage replaced `pkg-config` with `pkgconf` in `recipe/recipe.yaml`, as "
        "conda-forge's linter asks, and labeled the pull request for automatic "
        "merging. Every requirement already matches demo 3.3.1, as declared in "
        "its `pyproject.toml`."
    )


def test_the_comment_names_both_kinds_of_change(
    tmp_path: Path, names: NameSources
) -> None:
    stale = recipe_text("build", "pkg-config").replace("attrs >=22", "attrs >=21")
    forge, _ = update(tmp_path, names, stale)

    (body,) = forge.comments
    assert body.startswith(
        "swage updated `recipe/recipe.yaml` to match demo 3.3.1, as declared in "
        "its `pyproject.toml`, and replaced `pkg-config` with `pkgconf`, as "
        "conda-forge's linter asks. It labeled the pull request for automatic "
        "merging."
    )


def test_a_held_push_claims_no_match_it_did_not_make(
    tmp_path: Path, names: NameSources
) -> None:
    """The comment leaving the label off has no words to spare (DESIGN.md
    §3.2), so it names no edit; the commit does."""
    forge, _ = update(
        tmp_path, names, recipe_text("build", "pkg-config"), trust="propose"
    )

    (body,) = forge.comments
    assert body.startswith("swage updated `recipe/recipe.yaml` and pushed it.")
    assert "to match" not in body


def test_a_completed_test_matrix_is_not_said_to_match_the_release(
    tmp_path: Path, names: NameSources
) -> None:
    """The other convention swage applies (DESIGN.md §9.6)."""
    tested = recipe_text("build", "pkgconf") + (
        "\ntests:\n"
        "  - python:\n"
        "      imports:\n"
        "        - demo\n"
        "      python_version: ${{ python_min }}.*\n"
    )
    forge, record = update(tmp_path, names, tested, also="test_matrix: auto\n")

    assert record.outcome == "automerge"
    (body,) = forge.comments
    assert body.startswith(
        'swage added `"*"` to the python test in `recipe/recipe.yaml`, so it also '
        "runs on the newest Python, and labeled the pull request for automatic "
        "merging. Every requirement already matches demo 3.3.1"
    )
