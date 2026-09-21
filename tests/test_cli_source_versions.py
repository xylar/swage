"""Tests for pushing a source-version correction (v1 §3.6.5; DESIGN.md §9.8).

The correction is made before planning and the plan is against the corrected
recipe, so what is worth pinning is that the change is still measured from the
recipe as read: a run whose only change is the correction pushes it, the diff
shows it, and the commit says what moved and why.

The fixtures are `test_forge_source_versions`'s shape -- a recipe building a
library and a helper at a version of its own, with the library pinning the
helper exactly -- and the harness is `test_cli_update`'s.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import tarfile
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
from .test_cli_update import NEW_SHA, FakeForge


def sdist(name: str, version: str, dependencies: str) -> bytes:
    """A one-file sdist declaring ``name`` at ``version``."""
    pyproject = (
        "[build-system]\n"
        'requires = ["flit_core==3.12.0"]\n'
        "\n"
        "[project]\n"
        f'name = "{name}"\n'
        f'version = "{version}"\n'
        f"dependencies = [{dependencies}]\n"
    )
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        payload = pyproject.encode("utf-8")
        info = tarfile.TarInfo(f"{name}-{version}/pyproject.toml")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


#: The library the bot bumped, which pins the helper at 1.3.1.
LIB = sdist("demo", "3.3.1", '"demo-helper==1.3.1"')
#: The helper the recipe builds, a version behind; and the one it should.
OLD_HELPER = sdist("demo-helper", "1.3.0", '"attrs>=22"')
NEW_HELPER = sdist("demo-helper", "1.3.1", '"attrs>=22"')

URLS = {
    "https://example.invalid/demo-3.3.1.tar.gz": LIB,
    "https://example.invalid/demo-helper-1.3.0.tar.gz": OLD_HELPER,
    "https://example.invalid/demo-helper-1.3.1.tar.gz": NEW_HELPER,
}


def recipe_text(helper_version: str, helper: bytes) -> str:
    return f"""\
schema_version: 1

context:
  version: "3.3.1"
  helper_version: "{helper_version}"
  python_min: '3.10'

recipe:
  name: demo-split
  version: ${{{{ version }}}}

source:
  - url: https://example.invalid/demo-${{{{ version }}}}.tar.gz
    sha256: {hashlib.sha256(LIB).hexdigest()}
    target_directory: demo
  - url: https://example.invalid/demo-helper-${{{{ helper_version }}}}.tar.gz
    sha256: {hashlib.sha256(helper).hexdigest()}
    target_directory: helper

outputs:
  - package:
      name: demo
    build:
      noarch: python
    requirements:
      host:
        - python ${{{{ python_min }}}}.*
        - pip
        - flit-core ==3.12.0
      run:
        - python >=${{{{ python_min }}}}
        - demo-helper ==${{{{ helper_version }}}}
  - package:
      name: demo-helper
      version: ${{{{ helper_version }}}}
    build:
      noarch: python
    requirements:
      host:
        - python ${{{{ python_min }}}}.*
        - pip
        - flit-core ==3.12.0
      run:
        - python >=${{{{ python_min }}}}
        - attrs >=22
"""


#: What the pull request carries: every dependency line already matches
#: upstream, and only the helper's pin is stale.
STALE = recipe_text("1.3.0", OLD_HELPER)
#: What swage should push.
CORRECTED = recipe_text("1.3.1", NEW_HELPER)


def fetcher(url: str, timeout: float = 60.0) -> bytes:
    if url not in URLS:
        raise ForgeError(f"{url}: download failed: HTTP 404")
    return URLS[url]


@pytest.fixture
def names() -> NameSources:
    return NameSources(
        StaticPackageIndex.of("demo", "demo-helper", "attrs", "flit-core"),
        MappingLayer("grayskull pypi mapping", {}),
    )


def tree_at(tmp_path: Path, trust: str) -> Any:
    """The shipped quirks database, with `demo` opted in at one rung."""
    root = tmp_path / f"config-{trust}"
    shutil.copytree(CONFIG_ROOT, root)
    (root / "feedstocks" / "demo.yaml").write_text(
        f"feedstock: demo\ntrust: {trust}\nsource_versions: auto\n", encoding="utf-8"
    )
    return load_config(root)


def stale() -> FakeForge:
    return FakeForge(FakeGitHub(pulls=[pull()], files={"recipe/recipe.yaml": STALE}))


def update(
    forge: FakeForge, tree: Any, names: NameSources, tmp_path: Path, write: bool = True
) -> Any:
    run = run_update(
        GitHub(run=forge),
        Git(run=forge, root=tmp_path / "clones"),
        tree,
        ["demo"],
        names,
        write=write,
        fetch=fetcher,
    )
    return run.feedstocks[0]


def test_a_correction_alone_is_a_change_and_is_pushed(
    tmp_path: Path, names: NameSources
) -> None:
    forge = stale()
    record = update(forge, tree_at(tmp_path, "propose"), names, tmp_path)

    assert record.outcome == "needs-review"
    assert record.reason == "+2 -2 in the recipe"
    assert record.pushed == NEW_SHA
    written = tmp_path / "clones" / "demo-7" / "recipe" / "recipe.yaml"
    assert written.read_text(encoding="utf-8") == CORRECTED


def test_the_diff_is_against_the_recipe_as_read(
    tmp_path: Path, names: NameSources
) -> None:
    """`recipe.diff` in the run directory shows the `context` and `sha256` edit."""
    record = update(stale(), tree_at(tmp_path, "propose"), names, tmp_path)

    assert record.current_recipe == STALE
    assert record.rendered_recipe == CORRECTED


def test_the_commit_and_the_record_say_what_moved(
    tmp_path: Path, names: NameSources
) -> None:
    forge = stale()
    record = update(forge, tree_at(tmp_path, "propose"), names, tmp_path)

    moved = "demo-helper 1.3.0 to 1.3.1, which demo requires"
    (commit,) = forge.wrote("git", "commit")
    assert moved in commit[commit.index("--message") + 1]
    assert f"moved {moved}" in record.notes


def test_a_dry_run_lands_in_the_same_bucket(tmp_path: Path, names: NameSources) -> None:
    forge = stale()
    record = update(forge, tree_at(tmp_path, "propose"), names, tmp_path, write=False)

    assert record.outcome == "needs-review"
    assert record.reason == "+2 -2 in the recipe"
    assert forge.order == []


def test_at_trust_auto_the_correction_is_pushed_and_labeled(
    tmp_path: Path, names: NameSources
) -> None:
    forge = stale()
    record = update(forge, tree_at(tmp_path, "auto"), names, tmp_path)

    assert record.outcome == "automerge"
    assert forge.order == ["clone", "commit", "push", "unlabel", "label"]
