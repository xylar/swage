"""Reading and writing the run directory (v1 §9; DESIGN.md §11.1).

The directory is disposable; `run.json` inside it is a contract, so writing is
plain and reading validates. A `schema` this swage does not know is refused;
v1's are read through `from_v1`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from swage.cache import cache_root

from .errors import ReportError
from .record import SCHEMA_VERSION, V1_SCHEMAS, Run, from_v1

__all__ = [
    "DECLARATIONS_DIR",
    "RECIPES_DIR",
    "RUN_FILE",
    "all_runs",
    "latest_run",
    "read_run",
    "run_directory",
    "runs_since",
    "write_declarations",
    "write_recipes",
    "write_run",
]

RUN_FILE = "run.json"

#: Where `write_recipes` puts the renderings, under the run directory.
RECIPES_DIR = "recipes"

#: Where `write_declarations` puts the diffs, under the run directory.
DECLARATIONS_DIR = "declarations"

#: How a run directory spells the moment it started. Written and read in one
#: place, because the name is in UTC and nothing in the name says so.
_STAMP = "%Y-%m-%dT%H-%M-%S"


def run_directory(when: datetime | None = None, root: Path | None = None) -> Path:
    """The directory this run writes to, named for when it started."""
    stamp = (when or datetime.now(UTC)).strftime(_STAMP)
    return (root or cache_root()) / "runs" / stamp


def runs_since(cutoff: datetime, root: Path | None = None) -> tuple[Path, ...]:
    """Every run directory started at or after ``cutoff``, oldest first.

    Read from the directory name, which is the timestamp and cannot move when
    the directory is copied. A name that does not parse, or a directory with
    no `run.json`, is skipped.
    """
    runs = (root or cache_root()) / "runs"
    if not runs.is_dir():
        return ()
    found = []
    for directory in runs.iterdir():
        if not (directory / RUN_FILE).is_file():
            continue
        try:
            started = datetime.strptime(directory.name, _STAMP).replace(tzinfo=UTC)
        except ValueError:
            continue
        if started >= cutoff:
            found.append(directory)
    return tuple(sorted(found))


def all_runs(root: Path | None = None) -> tuple[Path, ...]:
    """Every run directory this machine has, oldest first."""
    return runs_since(datetime.fromtimestamp(0, UTC), root)


def latest_run(root: Path | None = None) -> Path | None:
    """The most recent run directory, or None where there has never been one.

    Sorted by name, which is the timestamp and cannot move. Only a directory
    holding a `run.json` counts.
    """
    runs = (root or cache_root()) / "runs"
    if not runs.is_dir():
        return None
    found = sorted(
        directory for directory in runs.iterdir() if (directory / RUN_FILE).is_file()
    )
    return found[-1] if found else None


def write_run(record: Run, directory: Path) -> Path:
    """Write ``run.json`` into ``directory``, creating it."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / RUN_FILE
    path.write_text(
        json.dumps(record.model_dump(by_alias=True), indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def write_recipes(record: Run, directory: Path) -> list[Path]:
    """Write each feedstock's rendered recipe, and the one it would replace.

    Both sides, so the comparison afterwards needs no network. This writes
    to the run directory and nothing else.
    """
    written: list[Path] = []
    for feedstock in record.feedstocks:
        if not feedstock.rendered_recipe:
            continue
        target = directory / RECIPES_DIR / feedstock.feedstock
        target.mkdir(parents=True, exist_ok=True)
        for name, text in (
            ("recipe.yaml", feedstock.rendered_recipe),
            ("recipe.before.yaml", feedstock.current_recipe),
        ):
            if not text:
                continue
            path = target / name
            path.write_text(text, encoding="utf-8")
            written.append(path)
    return written


def write_declarations(record: Run, directory: Path) -> list[Path]:
    """Write what this release did to each unread feedstock's declaration
    (v1 §3.6.8). The summary prints the first lines; this is where the rest
    is.
    """
    written: list[Path] = []
    for feedstock in record.feedstocks:
        if not feedstock.declaration_diff:
            continue
        target = directory / DECLARATIONS_DIR
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{feedstock.feedstock}.diff"
        path.write_text(feedstock.declaration_diff, encoding="utf-8")
        written.append(path)
    return written


def read_run(directory: Path) -> Run:
    """Read a run back, refusing a record this swage cannot read faithfully.

    A v1 run is mapped, feedstock by feedstock, before it is validated, so
    the model never sees a v1 field.
    """
    path = directory / RUN_FILE if directory.is_dir() else directory
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReportError(f"{path}: no run artifact there") from exc
    except json.JSONDecodeError as exc:
        raise ReportError(f"{path}: is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ReportError(f"{path}: is not a run record")
    version = payload.get("schema")
    if version in V1_SCHEMAS:
        feedstocks = payload.get("feedstocks", ())
        if not isinstance(feedstocks, list):
            raise ReportError(f"{path}: is not a run record")
        payload = {
            **payload,
            "schema": SCHEMA_VERSION,
            "feedstocks": [
                from_v1(entry) if isinstance(entry, dict) else entry
                for entry in feedstocks
            ],
        }
    elif version != SCHEMA_VERSION:
        raise ReportError(
            f"{path}: schema {version!r}, but this swage reads "
            f"{SCHEMA_VERSION}\n"
            "  the run artifact is disposable -- rerun the command that made it"
        )

    try:
        return Run.model_validate(payload)
    except ValidationError as exc:
        raise ReportError(f"{path}: is not a run record swage can read: {exc}") from exc
