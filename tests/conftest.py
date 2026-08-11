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
