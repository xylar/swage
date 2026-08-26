"""`swage scan` -- read everything, decide everything, change nothing.

This is the default gesture (DESIGN.md 8): it reports the plan and the trust
verdict per feedstock and touches nothing at all. Every layer below already
does its own job, and the reading, planning and gating are `consider`'s, shared
with `update` so that the two commands cannot reach different answers about the
same feedstock.

**Nothing here writes to a feedstock, and nothing here can.** What `scan` does
about a pull request is `do_nothing`, passed in; the write path is `update`'s
and is not reachable from this module. Every call `scan` provokes is a read,
through the choke point that passes `--method GET` precisely so that a read
cannot become a write by omission (DESIGN.md 3.5).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from swage.config import ConfigTree
from swage.forge import Fetcher, GitHub, download
from swage.report import RunRecord

from .consider import NameSources, consider_feedstock

__all__ = ["SCAN_DESCRIPTIONS", "run_scan"]

#: What the report's buckets mean when nothing was written (DESIGN.md 9).
#:
#: The record's vocabulary is unchanged -- `merge-ready` still means "passed
#: every gate, path A" whichever command produced it, so a run.json from `scan`
#: and one from `update` stay comparable. Only the wording differs, because a
#: bucket reading "pushed + labeled automerge" would describe something this
#: command is structurally incapable of doing.
SCAN_DESCRIPTIONS = {
    "merge-ready": "would push + label automerge -- `swage update` to do it",
    "proposed": "would push, and leave the labeling to you",
    "needs-migration": "v0 meta.yaml -- `swage update --migrate` converts in place",
}


def run_scan(
    github: GitHub,
    tree: ConfigTree,
    feedstocks: Sequence[str],
    names: NameSources,
    command: str = "swage scan",
    fetch: Fetcher = download,
    progress: Callable[[str], None] | None = None,
) -> RunRecord:
    """Scan every feedstock in ``feedstocks`` and assemble the run record."""
    started = datetime.now(UTC).isoformat(timespec="seconds")
    records = []
    for feedstock in feedstocks:
        if progress is not None:
            progress(feedstock)
        records.append(consider_feedstock(github, tree, feedstock, names, fetch))
    return RunRecord(command=command, started=started, feedstocks=tuple(records))
