"""`swage audit`: what the fleet would do if the bot filed tomorrow (v1 §8.2).

The pipeline is `scan`'s from `read` on, with a default branch for its subject
(DESIGN.md §12.2). What audit adds is the sweep, the hygiene notes and the
orphaned config files. It writes nothing, to a feedstock or to `config/`.
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
    UNMAINTAINED,
    NameSources,
    Subject,
    config_layers,
    consider,
    failure_reason,
)

__all__ = ["AUDIT_DESCRIPTIONS", "run_audit"]

#: What the buckets mean when the subject is a feedstock rather than a pull
#: request: the same vocabulary, subjunctive sentences.
AUDIT_DESCRIPTIONS = {
    "automerge": "a bot pull request would be pushed and labeled, unattended",
    "needs-review": "a decision is needed -- `swage draft <feedstock>` assembles it",
    "unchanged": "the recipe already matches the release it names",
    "needs-migration": (
        "v0 meta.yaml -- `swage update --migrate` converts it, then updates "
        "the conversion, which is the change counted here"
    ),
}


#: The `automerge` label conda-forge acts on; audit looks for one that has
#: stopped meaning anything.
AUTOMERGE = "automerge"

#: What a feedstock's own pull requests say about it, with no recipe read: facts
#: no other command reports (v1 §8.2).
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
ARCHIVED_FEEDSTOCK = "archived on GitHub, so nothing can be pushed or merged"
UNMAINTAINED_NOW_ARCHIVED = (
    "GitHub now reports this feedstock as archived, which says the same thing "
    "on its own; the `unmaintained` entry in config/feedstocks/{feedstock}.yaml "
    "can be dropped"
)
ORPHANED_CONFIG = (
    "not among the feedstocks you maintain, so its config applies to nothing\n"
    "  check the name, or delete config/feedstocks/{feedstock}.yaml"
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
    case in which a config file for a feedstock not in it means anything.
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
    """Config files for feedstocks that were not in a fleet-wide sweep: usually
    a typo in a filename, which loads and is never applied.
    """
    for feedstock in sorted(set(tree.feedstocks) - set(audited)):
        yield record(
            feedstock,
            "failed",
            stopped=ORPHANED_CONFIG.format(feedstock=feedstock),
            config_layers=(f"config/feedstocks/{feedstock}.yaml",),
        )


def _plural(count: int) -> str:
    return "" if count == 1 else "s"


def _hygiene(github: GitHub, feedstock: str) -> tuple[str, ...]:
    """What this feedstock's open pull requests say, with nothing planned. A
    read that fails is an advisory, not a failed feedstock.
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
            # The label is a flag the dispatched job reads, never what summons
            # it (docs/conda-forge.md), so this one will sit open forever.
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
    # Facts about the repository rather than the plan, gathered whatever the
    # plan turns out to be.
    notes = _hygiene(github, feedstock)

    try:
        repo = repository(github, feedstock)
        if repo.archived:
            # Read-only on GitHub: everything past this point produces a
            # proposal nobody could push. GitHub's answer wins over config's,
            # and where both say so the entry is a second copy of a fact GitHub
            # carries.
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
            # Still writable, and still nobody's to write to: archiving is a
            # request somebody else merges.
            return record(
                feedstock,
                "skipped",
                reason=UNMAINTAINED,
                stopped=config.unmaintained,
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
