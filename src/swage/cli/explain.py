"""`swage explain` -- why did it decide *that* (DESIGN.md 9.2).

The decisive choice here is not the layout but the input. **`explain` renders a
feedstock's record out of a run artifact and never recomputes it.** Because
these commands run unattended, the question being asked is almost never "what
would swage do now" but "why did it do that, at 03:00, while I was asleep" --
and by the time it is asked, upstream has moved on and config may have changed.
An `explain` that recomputed would answer a different question, confidently,
and a second computation path would be a second thing that can drift from the
planner. Rendering the stored record means `explain` cannot disagree with what
actually happened.

`--json` prints that record verbatim, so the human and machine views are two
renderings of one object rather than two implementations of it.

Nothing here reads a recipe, a channel, or GitHub. The only input is a
directory on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

from swage.report import (
    FeedstockRecord,
    ReportError,
    latest_run,
    read_run,
    render_explain,
)

__all__ = ["explain_feedstock", "resolve_run"]


def resolve_run(from_run: Path | None = None) -> Path:
    """The run directory to explain out of, named or most recent.

    Raises `ReportError` rather than falling back to recomputing, which is the
    whole point of the command: there is no answer to "why did it do that" if
    there is no record of it having done anything.
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
) -> tuple[str, FeedstockRecord]:
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
