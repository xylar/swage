"""Reading and writing the run directory (DESIGN.md 9).

The directory is disposable -- everything durable lives in git or in the
feedstocks themselves -- but `run.json` inside it is a contract, because
`swage explain --from-run` reads one back and a scheduler or dashboard would
too. So writing is plain and reading validates.

Reading rejects a record whose `schema` this swage does not know. A version
number nobody checks is decoration; the point of having one is that a shape
change is caught at the read, with the versions named, rather than surfacing
as a missing key three frames deeper.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from swage.cache import cache_root

from .errors import ReportError
from .model import SCHEMA_VERSION, RunRecord

__all__ = [
    "RECIPES_DIR",
    "RUN_FILE",
    "latest_run",
    "read_run",
    "run_directory",
    "runs_since",
    "write_recipes",
    "write_run",
]

RUN_FILE = "run.json"

#: Where `write_recipes` puts the renderings, under the run directory.
RECIPES_DIR = "recipes"

#: How a run directory spells the moment it started. Written and read in one
#: place, because the name is in UTC and nothing in the name says so -- a
#: caller parsing it by eye would have to guess, and would guess local.
_STAMP = "%Y-%m-%dT%H-%M-%S"


def run_directory(when: datetime | None = None, root: Path | None = None) -> Path:
    """The directory this run writes to, named for when it started."""
    stamp = (when or datetime.now(UTC)).strftime(_STAMP)
    return (root or cache_root()) / "runs" / stamp


def runs_since(cutoff: datetime, root: Path | None = None) -> tuple[Path, ...]:
    """Every run directory started at or after ``cutoff``, oldest first.

    `swage status` asks what became of what earlier runs did, so the window is
    over runs rather than over feedstocks. Read from the directory *name*
    rather than from the record inside it, which keeps the window cheap -- a
    run outside it is never opened -- and rests on the same fact `latest_run`
    does: the name is the timestamp, and it is the one thing about a run
    directory that cannot move when the directory is copied or restored.

    A directory whose name does not parse is not one swage wrote, and a
    directory with no `run.json` is a run that died before it recorded
    anything. Both are skipped rather than refused: the caller asked what
    happened in a window, and neither is an answer to that.
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


def latest_run(root: Path | None = None) -> Path | None:
    """The most recent run directory, or None where there has never been one.

    Sorted by name rather than by mtime, because the name *is* the timestamp
    and it is the one that cannot move: copying a run directory about, or
    restoring one from a backup, would reorder mtimes while leaving the
    question "which run happened last" with the same answer as before.

    Only a directory holding a `run.json` counts. A run that died part way
    through leaves the directory behind, and picking it would answer
    `swage explain` out of an artifact with nothing in it.
    """
    runs = (root or cache_root()) / "runs"
    if not runs.is_dir():
        return None
    found = sorted(
        directory for directory in runs.iterdir() if (directory / RUN_FILE).is_file()
    )
    return found[-1] if found else None


def write_run(record: RunRecord, directory: Path) -> Path:
    """Write ``run.json`` into ``directory``, creating it."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / RUN_FILE
    path.write_text(
        json.dumps(record.model_dump(by_alias=True), indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def write_recipes(record: RunRecord, directory: Path) -> list[Path]:
    """Write each feedstock's rendered recipe, and the one it would replace.

    swage already renders every recipe it plans -- G7 is a byte comparison
    against exactly this text (DESIGN.md 5.3) -- and until now threw it away.
    Keeping it is what makes DESIGN.md 10's differential validation a
    by-product of the sweep rather than a second tool: one `swage scan --all`
    leaves every rendering on disk, ready to diff against the feedstock and
    against the tools swage replaces.

    Both sides are written, so the comparison afterwards needs no network. A
    rendering alone answers "what would swage write"; beside `recipe.before`
    it also answers "what would change", which is the question anyone actually
    has.

    **This writes to a cache directory and nothing else.** The run directory is
    disposable and everything durable lives in git or in the feedstocks
    themselves; `scan` still has no code path that writes to a feedstock.
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


def read_run(directory: Path) -> RunRecord:
    """Read a run back, refusing a record this swage cannot read faithfully."""
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
    if version != SCHEMA_VERSION:
        raise ReportError(
            f"{path}: schema {version!r}, but this swage reads "
            f"{SCHEMA_VERSION}\n"
            "  the run artifact is disposable -- rerun the command that made it"
        )

    try:
        return RunRecord.model_validate(payload)
    except ValidationError as exc:
        raise ReportError(f"{path}: is not a run record swage can read: {exc}") from exc
