"""`swage scan`: read everything, decide everything, change nothing (v1 §8).

The pipeline's, with `do_nothing` for its action (DESIGN.md §12.2); the write
path is `update`'s and is not reachable from here.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from swage.config import ConfigTree
from swage.forge import Fetcher, GitHub, download
from swage.run import Run

from .pipeline import NameSources, consider_feedstock

__all__ = ["SCAN_DESCRIPTIONS", "run_scan"]

#: What the report's buckets mean when nothing was written (v1 §9): the same
#: vocabulary, subjunctive sentences.
SCAN_DESCRIPTIONS = {
    "automerge": "would push + label automerge -- `swage update` to do it",
    "labeled": "would label automerge -- `swage update` to do it",
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
) -> Run:
    """Scan every feedstock in ``feedstocks`` and assemble the run record."""
    started = datetime.now(UTC).isoformat(timespec="seconds")
    records = []
    for feedstock in feedstocks:
        if progress is not None:
            progress(feedstock)
        records.append(consider_feedstock(github, tree, feedstock, names, fetch))
    return Run(command=command, started=started, feedstocks=tuple(records))
