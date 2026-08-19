"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The quirks database that ships with the repo.
CONFIG_ROOT = REPO_ROOT / "config"

WriteTree = Callable[[Mapping[str, str]], Path]


@pytest.fixture
def write_tree(tmp_path: Path) -> WriteTree:
    """Build a config tree from a mapping of relative path -> file contents."""

    def _write(files: Mapping[str, str]) -> Path:
        root = tmp_path / "config"
        for relative, contents in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8")
        root.mkdir(parents=True, exist_ok=True)
        return root

    return _write


@pytest.fixture(autouse=True)
def cache_elsewhere(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Keep the whole suite out of the cache the maintainer's swage uses.

    Every cache swage writes is found through ``XDG_CACHE_HOME``, so a test
    exercising anything that writes one writes the real one unless it says so
    itself. Most do say so; three `select_feedstocks` tests did not, and each
    run of `pixi run check` left the completion cache holding the two feedstock
    names those tests invented. TAB then offered two names out of 487 until the
    next run that happened to discover -- a broken shell completion, arriving
    from a green test suite, which is not a place anybody looks.

    Autouse rather than a fixture each test opts into, because opting in is the
    step that was missed. A test wanting its own root still sets the variable
    and wins, since `monkeypatch.setenv` runs after this.
    """
    root = tmp_path_factory.mktemp("cache")
    monkeypatch.setenv("XDG_CACHE_HOME", str(root))
    return root
