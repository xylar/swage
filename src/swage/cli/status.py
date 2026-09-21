"""`swage status`: what became of what earlier runs did (v1 §8).

It follows the pull request, not the feedstock, and writes nothing: a label
added once CI has finished summons nothing (docs/conda-forge.md), so a pull
request swage pushed to whose CI has gone green is `ready-to-merge` for a
person. A pull request still open is re-planned through the same pipeline
(DESIGN.md §12.2), never remembered.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from swage.config import ConfigError, ConfigTree
from swage.forge import (
    Fetcher,
    ForgeError,
    GitHub,
    NotFound,
    download,
    read_pull_request,
)
from swage.run import Outcome, Record, ReportError, Run, read_run, record

from .pipeline import NameSources, Subject, config_layers, consider, failure_reason

__all__ = [
    "OVERTAKEN",
    "STATUS_DESCRIPTIONS",
    "Followed",
    "followed",
    "parse_since",
    "read_runs",
    "run_status",
]

_DURATION = re.compile(r"^(\d+)([dh])$")
_UNITS = {"d": "days", "h": "hours"}

#: The outcomes a run leaves waiting on something other than swage: CI
#: finishing, or a person pressing merge.
_WAITING = frozenset({"awaiting-ci", "ready-to-merge"})

#: What the buckets mean in a report that re-planned and wrote nothing: the
#: write buckets go subjunctive, and reaching one means the pull request has
#: changed since the run that acted on it.
STATUS_DESCRIPTIONS = {
    "automerge": "changed since swage pushed -- `swage update` to push again",
    "needs-migration": "v0 meta.yaml -- `swage update --migrate` converts it in place",
}

#: Said of an open pull request a run acted on that no longer describes a
#: version change; nothing will close it on its own.
OVERTAKEN = "the branch it targets already has this version -- close it"


def parse_since(text: str) -> timedelta:
    """How far back `--since` reaches, written as `7d` or `36h`. Two units and
    no more.
    """
    match = _DURATION.match(text.strip())
    if match is None:
        raise ValueError(f"'{text}' is not a window like 7d or 36h")
    return timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})


@dataclass(frozen=True, order=True)
class Followed:
    """One pull request an earlier run acted on."""

    feedstock: str
    number: int


def followed(runs: Sequence[Run]) -> tuple[Followed, ...]:
    """Which pull requests in ``runs`` this command has a question about: the
    ones swage pushed to (`pushed` is set by the write path alone) and the
    ones a run left waiting. Deduplicated on the pull request, not the
    feedstock.
    """
    seen = {
        Followed(record.feedstock, record.pull_request)
        for run in runs
        for record in run.feedstocks
        if record.pull_request is not None
        and (record.pushed or record.outcome in _WAITING)
    }
    return tuple(sorted(seen))


def read_runs(directories: Sequence[Path]) -> tuple[tuple[Run, ...], int]:
    """Every run that can be read, and how many could not be.

    An unreadable run is skipped and counted, never listed, so a narrowed
    window is never silent.
    """
    records = []
    skipped = 0
    for directory in directories:
        try:
            records.append(read_run(directory))
        except ReportError:
            skipped += 1
    return tuple(records), skipped


def run_status(
    github: GitHub,
    tree: ConfigTree,
    runs: Sequence[Run],
    names: NameSources,
    command: str = "swage status",
    fetch: Fetcher = download,
    progress: Callable[[str], None] | None = None,
) -> Run:
    """Ask what became of every pull request ``runs`` acted on."""
    started = datetime.now(UTC).isoformat(timespec="seconds")
    records = []
    for item in followed(runs):
        if progress is not None:
            progress(item.feedstock)
        records.append(_follow(github, tree, item, names, fetch))
    return Run(command=command, started=started, feedstocks=tuple(records))


def _follow(
    github: GitHub,
    tree: ConfigTree,
    item: Followed,
    names: NameSources,
    fetch: Fetcher,
) -> Record:
    """One pull request: what became of it, and what it needs now."""
    feedstock = item.feedstock
    try:
        config = tree.for_feedstock(feedstock)
    except ConfigError as exc:
        return record(feedstock, "failed", stopped=str(exc))
    layers = config_layers(tree, feedstock, config)
    about = _recorder(feedstock, item.number, layers)

    try:
        outcome = read_pull_request(github, feedstock, item.number)
    except NotFound:
        # The feedstock was renamed or removed under a pull request swage
        # touched. Nothing here can say which, and both want a person.
        return about("failed", stopped="the pull request is no longer there")
    except ForgeError as exc:
        return about("failed", stopped=failure_reason(exc))

    if outcome.merged:
        return about(
            "merged",
            reason="merged since the run that acted on it",
            head=outcome.pull.head_sha,
        )
    if not outcome.open:
        return about(
            "closed",
            reason="closed without merging -- swage's commit was not taken",
            head=outcome.pull.head_sha,
        )

    considered = consider(
        github, config, Subject.pull_request(outcome.pull), names, layers, fetch
    )
    if considered is not None:
        return considered
    # It no longer bumps a version, and for a pull request an earlier run
    # planned that means the branch it targets has caught up with it.
    return about("needs-review", reason=OVERTAKEN, head=outcome.pull.head_sha)


def _recorder(
    feedstock: str, number: int, layers: Sequence[str]
) -> Callable[..., Record]:
    """Every record this command writes names the pull request it followed."""

    def about(outcome: Outcome, **rest: Any) -> Record:
        return record(
            feedstock,
            outcome,
            pull_request=number,
            config_layers=layers,
            **rest,
        )

    return about
