"""Clone a pull request's branch and push a commit to it (DESIGN.md 3.5, 5.1).

This is the first thing in swage that writes anywhere but a cache directory,
and its shape is dictated by facts about conda-forge's bot rather than by
convenience.

**The branch is not on the feedstock.** The bot files from
`regro-cf-autotick-bot/<feedstock>-feedstock`, a fork, and a commit on a pull
request belongs to the *head* repository. So the clone target is
`pull.head_repo` and the push names `pull.head_ref` as an explicit refspec --
which also means `push.default` cannot decide anything, and a maintainer whose
git is configured to push nothing by default (as this one's is) gets the same
behaviour as everyone else.

**The clone is per run, and is never reused.** Reusing one means a sync path
-- fetch, reset, clean -- plus a check that `origin` still points where it did,
and the failure mode of getting any of that wrong is pushing a tree assembled
from somebody else's branch. A shallow single-branch clone of a feedstock is
small enough that starting fresh costs less than the code to reuse safely, and
it leaves the exact tree swage pushed sitting in the run directory afterwards,
which is the artifact you want when a push turns out to have been wrong.

**The head is pinned to what swage read.** The plan was computed against the
recipe at `pull.head_sha`, and cloning a *branch* gets whatever its tip is now.
If the bot has pushed since, those are different commits and the rendering is
against a base that no longer exists -- so the clone is checked against the
SHA and refuses rather than pushing a recipe reconciled from a stale read.

Nothing here decides *whether* to push. That is the trust ladder's job
(DESIGN.md 5.4), and keeping the decision out of the mechanism is what makes
the mechanism safe to test.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import Path

from swage.cache import cache_root

from .discover import BotPullRequest
from .errors import ForgeError
from .feedstock import RECIPE_V1
from .github import Runner, run_gh

__all__ = ["COMMIT_SUBJECT", "CO_AUTHOR", "Git", "Pushed", "commit_message"]

#: The subject swage writes on every recipe commit. Fixed rather than composed
#: per feedstock: it appears in several hundred repositories' histories, and a
#: subject that varies is one nobody can search for.
COMMIT_SUBJECT = "Reconcile recipe dependencies with upstream metadata"

#: swage claims co-authorship rather than authorship. The commit is authored by
#: whoever ran swage -- they are accountable for it, and the credentials that
#: pushed it are theirs -- but a feedstock's `git log` should still say plainly
#: which commits a tool wrote, for the same reason CLAUDE.md asks for the
#: equivalent trailer in this repository: the moment that matters is somebody
#: bisecting a feedstock to find out why a dependency changed.
CO_AUTHOR = "Co-Authored-By: swage <noreply@github.com>"

#: Where git conventionally wraps a commit body. The prose is wrapped to it
#: and the metadata source is not, because feedstock names run long enough to
#: overflow the line on their own -- `apache-airflow-providers-amazon 9.34.0`
#: puts the sentence at 95 columns before the URL is even reached.
WIDTH = 72

#: Where clones live under the run directory, so the tree swage pushed is
#: still on disk beside the record of why it pushed it.
CLONES = "clones"


@dataclass(frozen=True)
class Pushed:
    """What a push left behind, for the record and for a human to look at."""

    #: The commit swage created, which is the new head of the pull request.
    #: `swage status` needs it to tell its own commit from a later bot one.
    sha: str
    path: Path


def commit_message(release: str, source: str) -> str:
    """The commit swage writes to a feedstock, whole.

    The body says which release was read and out of which file, and says
    nothing about the gates. It is tempting to write "every requirement is
    attributed" -- that is G1's claim and it reads well -- but a gate failure
    does not stop the push (DESIGN.md 5.4), so the commit would assert
    something false on exactly the feedstocks somebody is most likely to be
    reading it on. What went wrong belongs in the comment, which is written
    per pull request and can be true.

    The source gets a line to itself because it cannot be wrapped: it is a
    sdist URL or a path-and-tag inside a monorepo, either of which runs past
    the column prose stops at, and breaking one leaves something nobody can
    paste back into anything.
    """
    lead = textwrap.fill(
        f"Written by swage from {release}, whose metadata was read from:",
        WIDTH,
        break_long_words=False,
        break_on_hyphens=False,
    )
    return f"{COMMIT_SUBJECT}\n\n{lead}\n{source}\n\n{CO_AUTHOR}\n"


class Git:
    """Clone, commit and push, through the same injectable runner as GitHub."""

    def __init__(self, run: Runner = run_gh, root: Path | None = None) -> None:
        self._run = run
        self._root = root if root is not None else cache_root() / CLONES

    def push_recipe(self, pull: BotPullRequest, recipe: str, message: str) -> Pushed:
        """Put ``recipe`` on ``pull``'s branch as one commit, and push it.

        The whole unit, because there is no useful state in between: a clone
        with an uncommitted change in it is not something a caller can do
        anything with, and a commit that is not pushed is a commit that will
        be thrown away with the run directory.
        """
        directory = self._clone(pull)
        (directory / RECIPE_V1).write_text(recipe, encoding="utf-8")
        self._git(directory, "add", "--", RECIPE_V1)
        self._git(directory, "commit", "--message", message)
        # Never `--force`: swage adds a commit to somebody's branch and has no
        # business rewriting what is already on it.
        self._git(directory, "push", "origin", f"HEAD:{pull.head_ref}")
        sha = self._git(directory, "rev-parse", "HEAD").strip()
        return Pushed(sha=sha, path=directory)

    def _clone(self, pull: BotPullRequest) -> Path:
        if not pull.head_repo:
            raise ForgeError(
                f"{pull.feedstock}#{pull.number}: the pull request's head "
                "repository no longer exists, so there is no branch to push to"
            )
        self._root.mkdir(parents=True, exist_ok=True)
        directory = self._root / f"{pull.feedstock}-{pull.number}"
        # Through `gh` rather than `git clone` so the remote is built with the
        # protocol and credentials the maintainer's GitHub CLI is already set
        # up with; swage handles no token of its own anywhere.
        self._run(
            [
                "gh",
                "repo",
                "clone",
                pull.head_repo,
                str(directory),
                "--",
                "--branch",
                pull.head_ref,
                "--single-branch",
                "--depth",
                "1",
            ]
        )
        head = self._git(directory, "rev-parse", "HEAD").strip()
        if head != pull.head_sha:
            raise ForgeError(
                f"{pull.feedstock}#{pull.number}: {pull.head_ref} is at {head}, "
                f"but swage planned against {pull.head_sha}\n"
                "  the bot pushed while swage was reading -- rerun to plan "
                "against the new commit"
            )
        return directory

    def _git(self, directory: Path, *argv: str) -> str:
        """One git call, addressed by `-C` so the runner needs no cwd.

        That is what lets git and `gh` share `Runner`: every call is a whole
        argv that says where it acts, so a fake sees the sequence rather than
        having to model a working directory.
        """
        return self._run(["git", "-C", str(directory), *argv])
