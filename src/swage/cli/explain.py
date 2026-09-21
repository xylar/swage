"""`swage explain`: why did it decide *that* (v1 §9.2; DESIGN.md §11.3).

It renders a feedstock's record out of a run artifact and never recomputes it.
`--json` prints that record verbatim. The only input is a directory on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

from swage.run import Record, ReportError, latest_run, read_run, render_explain

__all__ = ["explain_feedstock", "resolve_run"]


def resolve_run(from_run: Path | None = None) -> Path:
    """The run directory to explain out of, named or most recent. Raises
    `ReportError` rather than recomputing.
    """
    if from_run is not None:
        return from_run
    found = latest_run()
    if found is None:
        raise ReportError(
            "no run to explain\n"
            "  `swage explain` renders the record of a run rather than working "
            "the answer out again, so it needs one to have happened\n"
            "  run `swage scan` first, or name an older run with --from-run"
        )
    return found


def explain_feedstock(
    feedstock: str, directory: Path, as_json: bool = False
) -> tuple[str, Record]:
    """Render one feedstock's record out of the run in ``directory``."""
    run = read_run(directory)
    record = run.find(feedstock)
    if record is None:
        raise ReportError(
            f"{directory}: this run has no record of {feedstock!r}\n"
            f"  it covered {len(run.feedstocks)} feedstock(s)"
            + (f" as `{run.command}`" if run.command else "")
            + "\n  scan it, or name the run that did with --from-run"
        )
    if as_json:
        # The same object `run.json` holds, so anything reading this is
        # reading the contract rather than scraping the rendering.
        return json.dumps(record.model_dump(by_alias=True), indent=2), record
    return render_explain(record, run=directory.name), record
