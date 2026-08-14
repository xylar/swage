"""The two ways a pull request swage has judged reaches `main`.

Either conda-forge merges it, once swage has pushed a commit and armed the
label, or swage merges it itself because nothing it could push would ever start
the CI run that arms anything (DESIGN.md 5.1, 5.2). Both live here, and each
exists because the obvious spelling of it is wrong.

**Adding a label that is already there produces no timeline event.** The
dispatched automerge job merges only if no commit appears after the most recent
`labeled` event whose label is `automerge` (DESIGN.md 2.2), so a pull request
that already carried the label before swage pushed would be measured against
the *original* event -- which is now older than swage's commit -- and the job
would strip the label and refuse. Re-adding it changes nothing, because GitHub
treats it as a no-op. Removing it and adding it back is what produces a fresh
event with a later timestamp.

**And a merge is pinned to the commit that was checked.** swage merges because
it verified that this recipe needs no change and that this commit's CI passed;
the bot can push again in the seconds between, and a merge that did not name
the commit would quietly take the new one (DESIGN.md 5.2).

Nothing here decides whether to arm or merge anything. That is the trust ladder
(DESIGN.md 5.4), and it lives in the command.
"""

from __future__ import annotations

import textwrap

from .discover import BotPullRequest
from .github import GitHub
from .repo import CO_AUTHOR, WIDTH

__all__ = ["AUTOMERGE", "arm_automerge", "merge_message", "merge_pull"]

#: conda-forge's own label, which every feedstock already has. swage applies
#: this one and creates none of its own (DESIGN.md 5.4).
AUTOMERGE = "automerge"


def arm_automerge(github: GitHub, pull: BotPullRequest) -> None:
    """Give ``pull`` an `automerge` event newer than swage's commit.

    Called as the very next thing after a successful push and never before one
    (DESIGN.md 2.2, 5.5): labelling first guarantees the label is stripped,
    and pushing without labelling leaves a `[bot-automerge]` pull request
    *less* automated than swage found it, because swage's commit is not a
    bot's and breaks the all-commits-from-a-bot test forever.
    """
    github.unlabel(pull.repo, pull.number, AUTOMERGE)
    github.label(pull.repo, pull.number, AUTOMERGE)


def merge_pull(github: GitHub, pull: BotPullRequest, release: str) -> None:
    """Merge ``pull``, provided its head is still the commit swage checked.

    The subject is conda-forge's own convention -- the pull request's title and
    its number -- so that a merge swage made reads in `git log` like every
    other merge in that feedstock rather than like something a stranger did.
    """
    github.merge(
        pull.repo,
        pull.number,
        f"{pull.title} (#{pull.number})",
        merge_message(release),
        pull.head_sha,
    )


def merge_message(release: str) -> str:
    """The body of the merge commit swage creates.

    One wrapped sentence and the trailer, for the reasons `commit_message` is
    written the way it is: this lands in a feedstock's `git log` forever, and
    the moment it matters is somebody bisecting to find out why a dependency
    changed and wanting to know at once that a tool did this rather than a
    person. The pull request holds the reasoning; a merge commit carrying a
    list of checks would be one nobody reads past.
    """
    body = textwrap.fill(
        f"The recipe already matched {release}, so swage changed nothing and "
        "merged once every check conda-forge requires had passed.",
        WIDTH,
        break_long_words=False,
        break_on_hyphens=False,
    )
    return f"{body}\n\n{CO_AUTHOR}\n"
