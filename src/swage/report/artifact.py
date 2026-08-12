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
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from .errors import ReportError
from .model import SCHEMA_VERSION, RunRecord

__all__ = ["RUN_FILE", "read_run", "run_directory", "write_run"]

RUN_FILE = "run.json"


def run_directory(when: datetime | None = None, root: Path | None = None) -> Path:
    """The directory this run writes to, named for when it started."""
    stamp = (when or datetime.now(UTC)).strftime("%Y-%m-%dT%H-%M-%S")
    return (root or _cache_root()) / "runs" / stamp


def _cache_root() -> Path:
    from_env = os.environ.get("XDG_CACHE_HOME")
    base = Path(from_env) if from_env else Path.home() / ".cache"
    return base / "swage"


def write_run(record: RunRecord, directory: Path) -> Path:
    """Write ``run.json`` into ``directory``, creating it."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / RUN_FILE
    path.write_text(
        json.dumps(record.model_dump(by_alias=True), indent=2) + "\n",
        encoding="utf-8",
    )
    return path


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
