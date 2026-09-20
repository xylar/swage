"""`swage audit` -- what the fleet would do if the bot filed tomorrow (8.2).

Every other command is driven by an open bot pull request, because that is what
there is to act on. The consequence is that swage has never looked at most of
what it maintains: a `scan --all` over 487 feedstocks plans 8 of them and
reports the other 479 as having no open bot pull request, which is true and
says nothing about them.

This one reads each feedstock's default branch and plans it there, so the
question it answers is **readiness**: if the bot filed a pull request for this
feedstock tomorrow, what would swage do with it? Answering that early is the
point, because the config decision that would hold a pull request can be made
before the pull request exists.

**Almost none of this is new.** The pipeline is `scan`'s from `read` on, with
a default branch for its subject (DESIGN.md §12.2). What audit adds is the
sweep, the hygiene notes, and the orphaned config files.

**It writes nothing**, to a feedstock or to `config/`. Audit produces the list;
`swage draft <feedstock> --execute` writes a config file, one at a time and
deliberately. An audit that filled in the quirks database would be exactly the
failure a required `reason` exists to prevent, at fleet scale.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime

from swage.config import ConfigError, ConfigTree
from swage.forge import (
    Fetcher,
    ForgeError,
    GitHub,
    NotFound,
    download,
    open_bot_pull_requests,
    repository,
    verify_ci,
)
from swage.run import Record, Run, record

from .pipeline import (
    BOT_BACKLOG_CAP,
    NameSources,
    Subject,
    config_layers,
    consider,
    failure_reason,
)

__all__ = ["AUDIT_DESCRIPTIONS", "run_audit"]

#: What the buckets mean when the subject is a feedstock rather than a pull
#: request. The vocabulary is unchanged on purpose -- an outcome is a statement
#: about the gates rather than about what was written, so a feedstock audit
#: holds is one a later `scan` must hold too, and two vocabularies would be two
#: things to keep in step.
#:
#: Only the sentences move, and every one of them goes subjunctive: audit has
#: no pull request in front of it and pushes nothing.
AUDIT_DESCRIPTIONS = {
    "automerge": "a bot pull request would be pushed and labeled, unattended",
    "needs-review": "a decision is needed -- `swage draft <feedstock>` assembles it",
    "unchanged": "the recipe already matches the release it names",
    "needs-migration": (
        "v0 meta.yaml -- `swage update --migrate` converts it, then updates "
        "the conversion, which is the change counted here"
    ),
}


#: The `automerge` label conda-forge acts on. Named here because audit looks
#: for one that has stopped meaning anything, which is the opposite of what
#: `forge.pulls` uses it for.
AUTOMERGE = "automerge"

#: What a feedstock's own pull requests say about it, with no recipe read and
#: no archive fetched. Each of these is invisible to every other command,
#: because every other command is looking at a pull request it means to act on
#: and each of these is about one nobody is going to act on (design-v1.md 8.2).
INERT_LABEL = (
    "pull request #{number} carries the `automerge` label and its CI has "
    "finished, so nothing will ever merge it -- merge it yourself"
)
BOT_GAVE_UP = (
    "{count} open bot pull requests, which is where the bot stops filing new "
    "ones -- no further version is offered until they clear"
)
ARCHIVED = (
    "the feedstock is archived and has {count} open bot pull request{s}, which "
    "nothing can push to and nothing can merge"
)
ARCHIVED_FEEDSTOCK = (
    "archived on GitHub, so nothing can be pushed to it, merged into it or "
    "labeled on it -- swage reads no further"
)
UNMAINTAINED_NOW_ARCHIVED = (
    "GitHub now reports this feedstock as archived, which says the same thing "
    "on its own; the `unmaintained` entry in config/feedstocks/{feedstock}.yaml "
    "can be dropped"
)
UNMAINTAINED = (
    "there is a config file for this feedstock and you do not maintain it, so "
    "nothing it says is ever applied -- check the name, or delete it"
)


def run_audit(
    github: GitHub,
    tree: ConfigTree,
    feedstocks: Sequence[str],
    names: NameSources,
    command: str = "swage audit",
    fetch: Fetcher = download,
    progress: Callable[[str], None] | None = None,
    complete: bool = False,
) -> Run:
    """Plan every feedstock in ``feedstocks`` on its own default branch.

    ``complete`` says this selection is the whole fleet, which is the only
    circumstance in which a config file for a feedstock *not* in it means
    anything. Over a family or a single feedstock every other config file is
    absent for the obvious reason, and reporting them would be noise that
    trained a reader to ignore the one time it mattered.
    """
    started = datetime.now(UTC).isoformat(timespec="seconds")
    records = [
        _audit(github, tree, feedstock, names, fetch)
        for feedstock in _with_progress(feedstocks, progress)
    ]
    if complete:
        records.extend(_unmaintained(tree, feedstocks))
    return Run(command=command, started=started, feedstocks=tuple(records))


def _with_progress(
    feedstocks: Sequence[str], progress: Callable[[str], None] | None
) -> Iterator[str]:
    for feedstock in feedstocks:
        if progress is not None:
            progress(feedstock)
        yield feedstock


def _unmaintained(tree: ConfigTree, audited: Sequence[str]) -> Iterator[Record]:
    """Config files for feedstocks that were not in a fleet-wide sweep.

    A quirks database going stale in the direction nobody looks. The usual
    cause is a typo in a filename, which is worse than a missing file: the
    config loads, validates, and is silently never applied to anything.
    """
    for feedstock in sorted(set(tree.feedstocks) - set(audited)):
        yield record(
            feedstock,
            "failed",
            stopped=UNMAINTAINED,
            config_layers=(f"config/feedstocks/{feedstock}.yaml",),
        )


def _plural(count: int) -> str:
    return "" if count == 1 else "s"


def _hygiene(github: GitHub, feedstock: str) -> tuple[str, ...]:
    """What this feedstock's open pull requests say, with nothing planned.

    Cheap -- one listing, and CI is only asked about where a pull request
    actually carries the label -- and the answers are ones nothing else
    reports. A read that fails is not worth failing a feedstock over: the plan
    is the substance of an audit and these are advisories beside it.
    """
    try:
        pulls = open_bot_pull_requests(github, feedstock, include_archived=True)
    except ForgeError:
        return ()

    notes = []
    if pulls and pulls[0].archived:
        notes.append(ARCHIVED.format(count=len(pulls), s=_plural(len(pulls))))
    elif len(pulls) >= BOT_BACKLOG_CAP:
        notes.append(BOT_GAVE_UP.format(count=len(pulls)))
    for pull in pulls:
        if pull.archived or AUTOMERGE not in pull.labels:
            continue
        try:
            status = verify_ci(github, pull)
        except ForgeError:
            continue
        if not status.pending:
            # The label is a flag the dispatched job reads, never the thing
            # that summons it (design-v1.md 2.1). With CI finished there is no
            # event left to dispatch on, so this one will sit open forever
            # looking exactly like a pull request about to merge.
            notes.append(INERT_LABEL.format(number=pull.number))
    return tuple(notes)


def _audit(
    github: GitHub,
    tree: ConfigTree,
    feedstock: str,
    names: NameSources,
    fetch: Fetcher,
) -> Record:
    """One feedstock, read where it lives rather than on a pull request."""
    try:
        config = tree.for_feedstock(feedstock)
    except ConfigError as exc:
        return record(feedstock, "failed", stopped=str(exc))
    layers = config_layers(tree, feedstock, config)
    # Facts about the repository rather than about the plan, so they are
    # gathered whatever the plan turns out to be -- including for a v0
    # feedstock whose conversion is refused, which is never planned at all and
    # can still be sitting on a pull request nothing will ever merge.
    notes = _hygiene(github, feedstock)

    try:
        repo = repository(github, feedstock)
        if repo.archived:
            # Read-only on GitHub. Everything past this point costs an archive
            # fetch and a plan, and produces a proposal nobody could ever push:
            # `apache-airflow-task-sdk` was reported PROPOSED for exactly that
            # reason. Stop here and say what it is.
            #
            # GitHub's answer wins over config's, and where both say so the
            # config entry has done its job and is now a second copy of a fact
            # GitHub carries. Saying that is what keeps the list from rotting
            # -- the same reason `_stale` reports a `supported` answer this
            # release has nothing to answer.
            return record(
                feedstock,
                "skipped",
                reason=ARCHIVED_FEEDSTOCK,
                config_layers=layers,
                notes=(
                    (*notes, UNMAINTAINED_NOW_ARCHIVED.format(feedstock=feedstock))
                    if config.unmaintained
                    else notes
                ),
            )
        if config.unmaintained:
            # Still writable, and still nobody's to write to. Archiving a
            # conda-forge feedstock is a request somebody else merges, so
            # between the decision and the archiving there is a window where
            # the repository looks exactly like a live one.
            return record(
                feedstock,
                "skipped",
                reason=config.unmaintained,
                config_layers=layers,
                notes=notes,
            )
        ref = repo.default_branch
    except NotFound:
        # A team with no repository behind it -- `all-members` is org-wide and
        # nothing in the team object says so.
        return record(
            feedstock,
            "unchanged",
            reason="no feedstock repository",
            config_layers=layers,
        )
    except ForgeError as exc:
        return record(
            feedstock,
            "failed",
            stopped=failure_reason(exc),
            config_layers=layers,
            notes=notes,
        )
    considered = consider(
        github, config, Subject.default_branch(ref, notes), names, layers, fetch
    )
    # Only a pull request can turn out not to be a version update.
    assert considered is not None
    return considered
