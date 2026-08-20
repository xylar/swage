"""The package imports and declares a version the build backend can read."""

from __future__ import annotations

import re
import subprocess
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


def test_no_tracked_file_carries_a_merge_conflict() -> None:
    """A conflict committed to `main` survived every check there is.

    `DESIGN.md` is the file every instruction in `CLAUDE.md` says to read
    first, and for one merge its gate table held both sides of a conflict and
    the markers between them. Nothing noticed: the suite reads the design's
    fenced examples and never the prose, and `git` had already been told the
    conflict was resolved.

    Markers are matched at the start of a line and with the trailing space
    `git` writes, so a diff or a shell heredoc quoted inside documentation is
    not mistaken for one.
    """
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.split("\0")
    markers = ("<<<<<<< ", "======= ", ">>>>>>> ")
    found = []
    for name in filter(None, tracked):
        path = REPO_ROOT / name
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if line.startswith(markers) or line == "=======":
                found.append(f"{name}:{number}: {line}")
    assert not found, "merge conflict markers left in tracked files:\n" + "\n".join(
        found
    )
