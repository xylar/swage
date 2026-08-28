"""`swage status` -- what became of what earlier runs did (DESIGN.md 8).

Every other command is driven by upstream: it reads a release, plans a recipe,
and reports what should happen. This one is driven by swage's own history. It
reads the runs in a window, takes every pull request those runs acted on or
left waiting, and asks GitHub what has happened to it since.

**It follows the pull request, not the feedstock.** A superseded pull request
and the one that superseded it are both real, and only one of them is the one
swage pushed to -- so "did my commit land" has to be asked by number. `scan` is
the command that asks about a feedstock.

**It writes nothing at all.** The design once had this command re-arm a pull
request left `DEGRADED` by a failed labeling call, and that cannot work.
conda-forge dispatches its automerge from CI status events, so a label added
once CI has finished summons nothing (DESIGN.md 2.1) -- and by the time a
report anybody reads the morning after runs, CI on the commit swage pushed has
long finished. What the re-arm was for is covered without writing anything: a
pull request swage pushed to, whose CI has since gone green, needs no change
and is mergeable, which is exactly `READY TO MERGE`. The reader presses the
button swage may not (DESIGN.md 5.2.2).

**A pull request still open is re-considered rather than remembered.** Saying
`READY TO MERGE` is a claim that the recipe needs no change *now*, and between
the two runs the pull request may have gained a commit, or the quirks database
may have gained the file that settles what held it. Re-planning through the
same path `scan` uses is what makes that claim true rather than inherited, and
it leaves no second implementation of a verdict to keep in step.
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
from swage.report import (
    FeedstockRecord,
    Outcome,
    ReportError,
    RunRecord,
    build_record,
    read_run,
)

from .consider import NameSources, config_layers, consider_pull, failure_reason

__all__ = [
    "DEFAULT_SINCE",
    "OVERTAKEN",
    "STATUS_DESCRIPTIONS",
    "Followed",
    "followed",
    "parse_since",
    "read_runs",
    "run_status",
]

#: How far back to look when nobody says, and what DESIGN.md 8's synopsis
#: writes. A week covers a maintainer who runs swage when they think of it.
DEFAULT_SINCE = "7d"

_DURATION = re.compile(r"^(\d+)([dh])$")
_UNITS = {"d": "days", "h": "hours"}

#: The outcomes a run leaves waiting on something other than swage. Both mean
#: the recipe needed no change, so nothing was pushed and nothing was labeled;
#: what they wait on is CI finishing, or a person pressing merge.
_WAITING = frozenset({"awaiting-ci", "ready-to-merge"})

#: What the buckets mean in a report that re-planned and wrote nothing.
#:
#: The write buckets go subjunctive, as they do in a dry run: `status` reaches
#: them through the same gates `update` does but pushed nothing, and "pushed +
#: labeled automerge" would claim an action this command cannot take. Reaching
#: one at all means the pull request has changed since the run that acted on
#: it, which is worth an `update`.
STATUS_DESCRIPTIONS = {
    "merge-ready": "changed since swage pushed -- `swage update` to push again",
    "proposed": "changed since swage pushed; needs your review before labeling",
    "needs-migration": "v0 meta.yaml -- `swage migrate` converts it",
}

#: Said of a pull request that is open, that a run acted on, and that no longer
#: describes a version change -- the branch it targets has caught up with it by
#: some other route. Nothing is left for it to do and nothing will close it on
#: its own, so it goes in front of a person rather than being reported as fine.
OVERTAKEN = "the branch it targets already has this version -- close it"


def parse_since(text: str) -> timedelta:
    """How far back `--since` reaches, written as `7d` or `36h`.

    Two units and no more. A window over runs somebody made by hand is the only
    thing this has to express, and a general duration parser would be inviting
    `--since 90m` to mean something.
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


def followed(runs: Sequence[RunRecord]) -> tuple[Followed, ...]:
    """Which pull requests in ``runs`` this command has a question about.

    Two kinds, and the rule is read off the record rather than off a list of
    buckets that would drift as buckets are added:

    - **swage pushed a commit to it.** `pushed` is set by the write path and by
      nothing else, so this is exactly the set of pull requests swage changed,
      whatever the gates then decided about labeling them.
    - **the run left it waiting.** Nothing was written -- the recipe already
      matched upstream -- and what it waits on is CI finishing or a person
      merging. A dry run produces these as truthfully as an executing one,
      which is why they are not filtered on `pushed`.

    Deduplicated on the pull request rather than on the feedstock, so a
    feedstock whose pull request was superseded inside the window is asked
    about twice and answered about twice. Those are two questions -- did the
    first one land, and what of the second -- and collapsing them would drop
    the one swage actually pushed to.
    """
    seen = {
        Followed(record.feedstock, record.pull_request)
        for run in runs
        for record in run.feedstocks
        if record.pull_request is not None
        and (record.pushed or record.outcome in _WAITING)
    }
    return tuple(sorted(seen))


def read_runs(directories: Sequence[Path]) -> tuple[tuple[RunRecord, ...], int]:
    """Every run that can be read, and how many could not be.

    A run written by a swage whose record shape has since changed is skipped
    rather than fatal: the command was asked what happened in a window, and one
    unreadable artifact in it is not an answer to that. The count is still
    reported, because narrowing a window in silence is how a report comes back
    clean by having looked at less than it claimed.

    **Counted rather than listed, and that came from running it.** The cache on
    a machine that has been developing swage held 48 runs across three older
    record shapes, every one of them inside a default window -- so a line each
    would have buried the report under its own preamble. The reason is the same
    for all of them and the artifact is disposable, so the number is the whole
    of what a reader can act on.
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
    runs: Sequence[RunRecord],
    names: NameSources,
    command: str = "swage status",
    fetch: Fetcher = download,
    progress: Callable[[str], None] | None = None,
) -> RunRecord:
    """Ask what became of every pull request ``runs`` acted on."""
    started = datetime.now(UTC).isoformat(timespec="seconds")
    records = []
    for item in followed(runs):
        if progress is not None:
            progress(item.feedstock)
        records.append(_follow(github, tree, item, names, fetch))
    return RunRecord(command=command, started=started, feedstocks=tuple(records))


def _follow(
    github: GitHub,
    tree: ConfigTree,
    item: Followed,
    names: NameSources,
    fetch: Fetcher,
) -> FeedstockRecord:
    """One pull request: what became of it, and what it needs now."""
    feedstock = item.feedstock
    try:
        config = tree.for_feedstock(feedstock)
    except ConfigError as exc:
        return build_record(feedstock, "failed", stopped=str(exc))
    layers = config_layers(tree, feedstock, config)
    record = _recorder(feedstock, item.number, layers)

    try:
        outcome = read_pull_request(github, feedstock, item.number)
    except NotFound:
        # The feedstock was renamed or removed under a pull request swage
        # touched. Nothing here can say which, and both want a person.
        return record("failed", stopped="the pull request is no longer there")
    except ForgeError as exc:
        return record("failed", stopped=failure_reason(exc))

    if outcome.merged:
        return record(
            "merged",
            detail="merged since the run that acted on it",
            head=outcome.pull.head_sha,
        )
    if not outcome.open:
        return record(
            "closed",
            detail="closed without merging -- swage's commit was not taken",
            head=outcome.pull.head_sha,
        )

    considered = consider_pull(github, config, outcome.pull, names, layers, fetch=fetch)
    if considered is not None:
        return considered
    # It no longer bumps a version, and for a pull request an earlier run
    # planned that means the branch it targets has caught up with it.
    return record("needs-review", detail=OVERTAKEN, head=outcome.pull.head_sha)


def _recorder(
    feedstock: str, number: int, layers: Sequence[str]
) -> Callable[..., FeedstockRecord]:
    """Every record this command writes names the pull request it followed."""

    def record(outcome: Outcome, **rest: Any) -> FeedstockRecord:
        return build_record(
            feedstock,
            outcome,
            pull_request=number,
            config_layers=layers,
            **rest,
        )

    return record
