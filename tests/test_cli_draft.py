"""Tests for `swage draft` (DESIGN.md 8.1).

The read harness is `test_cli_scan`'s. What matters here is not that files get
written -- that is `test_report_draft` -- but which ref the command reads a
feedstock at.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from swage.cli.consider import NameSources
from swage.cli.draft import run_draft
from swage.config import MappingLayer, load_config
from swage.forge import GitHub
from swage.mapping import StaticPackageIndex

from .conftest import CONFIG_ROOT
from .test_cli_scan import STALE_RECIPE, FakeGitHub, fetcher


class DraftGitHub(FakeGitHub):
    """The scan fake, plus the default branch a draft has to ask for.

    Answers `master`, so a test fails if anything goes back to assuming `main`.
    """

    def __init__(self, branch: str = "master", **rest: Any) -> None:
        super().__init__(**rest)
        self.branch = branch

    def __call__(self, argv: Sequence[str]) -> str:
        path = next(part for part in argv if "/" in part and not part.startswith("-"))
        if re.fullmatch(r"repos/conda-forge/[^/]+-feedstock", path):
            return json.dumps({"default_branch": self.branch})
        return super().__call__(argv)

    def _contents(self, path: str, argv: Sequence[str]) -> str:
        wanted = path.split("/contents/", 1)[1]
        if wanted == ".ci_support":
            return json.dumps([])
        return super()._contents(path, argv)


@pytest.fixture
def names() -> NameSources:
    return NameSources(
        StaticPackageIndex.of("requests", "pandas", "flit-core", "leftover"),
        MappingLayer("grayskull pypi mapping", {}),
    )


@pytest.fixture
def tree(tmp_path: Path) -> Any:
    root = tmp_path / "config"
    shutil.copytree(CONFIG_ROOT, root)
    (root / "feedstocks" / "demo.yaml").write_text(
        "feedstock: demo\ntrust: auto\n", encoding="utf-8"
    )
    return load_config(root)


# --- which ref it reads ------------------------------------------------------


def test_a_feedstock_is_drafted_at_its_own_default_branch(
    tmp_path: Path, tree: Any, names: NameSources
) -> None:
    """It used to read `main`, which is silently wrong on a `master` feedstock.

    Wrong in the worst available way: the recipe comes back missing, which
    reads as "this is still v0" -- an answer a maintainer would act on by
    reaching for `swage migrate`.
    """
    runner = DraftGitHub(
        branch="master", pulls=[], files={"recipe/recipe.yaml": STALE_RECIPE}
    )
    workbench, applied = run_draft(
        GitHub(run=runner),
        tree,
        "demo",
        names,
        root=tmp_path / "cache",
        fetch=fetcher(),
    )
    assert applied is None
    assert (workbench.directory / "FINDINGS.md").exists()
    assert any("ref=master" in argv for argv in runner.argvs)
