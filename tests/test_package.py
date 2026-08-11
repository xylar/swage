"""The package imports and declares a version the build backend can read."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import swage

REPO_ROOT = Path(__file__).resolve().parents[1]

# PEP 440, loosely: enough to catch a version that is not a version.
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(\.dev\d+|[ab]\d+|rc\d+)?$")


def test_version_is_a_release_identifier() -> None:
    assert VERSION_RE.match(swage.__version__), swage.__version__


def test_build_backend_reads_the_version_from_the_package() -> None:
    """One source of truth: hatchling reads __version__ rather than repeating it."""
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert "version" in pyproject["project"]["dynamic"]
    assert pyproject["tool"]["hatch"]["version"]["path"] == "src/swage/__init__.py"
