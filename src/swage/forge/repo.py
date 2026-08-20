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
behavior as everyone else.

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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from swage.cache import cache_root

from .checks import CONDA_FORGE_YML
from .discover import BotPullRequest
from .errors import ForgeError
from .feedstock import RECIPE_V0, RECIPE_V1
from .github import Runner, run_gh

__all__ = [
    "COMMIT_SUBJECT",
    "CONVERSION_SUBJECT",
    "CO_AUTHOR",
    "Git",
    "Pushed",
    "commit_message",
    "conversion_message",
]

#: The subject swage writes on every recipe commit. Fixed rather than composed
#: per feedstock: it appears in several hundred repositories' histories, and a
#: subject that varies is one nobody can search for.
COMMIT_SUBJECT = "Reconcile recipe dependencies with upstream metadata"

#: The subject on the conversion commit, fixed for the same reason. It says
#: what the commit did rather than naming a schema version, because "v0" and
#: "v1" are conda-forge's words for it and a feedstock's history is read by
#: people who have only ever seen one of the two formats.
CONVERSION_SUBJECT = "Convert the recipe to the new format"

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


def conversion_message(
    settings: Sequence[str],
    concerns: Sequence[str],
    damage: Sequence[str] = (),
    conditions: Sequence[str] = (),
) -> str:
    """The commit that converts a recipe, whole.

    Separate from the reconciliation commit rather than combined with it,
    because a combined diff is enormous -- `meta.yaml` deleted, `recipe.yaml`
    added, `conda-forge.yml` changed -- and the dependency edit, which is the
    part needing judgment, would be invisible inside it (DESIGN.md 7.1).

    **The body says what a reader of this repository needs, and nothing about
    swage's design.** This lands in several hundred repositories swage does
    not own, read by people who have never seen that design, and it is
    permanent. So: which tool did the conversion, what else changed and why,
    what swage found wrong with the result, what the converter could not carry,
    and what became of each condition the old recipe stated.

    **The last two are what makes the commit reviewable at all**, because the
    reviewer is reading this on GitHub, where the diff says only that every
    line changed. On a compiled recipe the conditions are the substance, so the
    ledger is the part of this message with the most in it -- and where a
    condition landed nowhere, `damage` above says so and quotes the line that
    went with it.

    ``damage`` comes first because it is the only part that means the recipe is
    *wrong* rather than worth a look, and its entries carry their own line
    breaks: the lines they quote are the finding, and reflowing a build command
    into prose is what hides a two-character difference in the middle of one.
    """
    body = [
        textwrap.fill(
            "Converted from the old recipe format by conda-recipe-manager. "
            "The whole file is rewritten, so there is no useful diff to read "
            "here. The dependency changes are in the commit after this one, "
            "on their own, where they can be reviewed line by line.",
            WIDTH,
        )
    ]
    if settings:
        body.append(
            textwrap.fill(
                "conda-forge.yml gains "
                + ", ".join(settings)
                + ". Without those, conda-forge would go on building this "
                "feedstock the old way and the converted recipe would not be "
                "used.",
                WIDTH,
            )
        )
    if damage:
        body.append(
            "The conversion is wrong here, and has to be fixed before this is "
            "merged:\n" + _listed(damage)
        )
    if concerns:
        body.append("The converter could not carry these over:\n" + _listed(concerns))
    if conditions:
        body.append(
            "What became of each condition the old recipe stated:\n"
            + "\n".join(f"  {row}" for row in conditions)
        )
    joined = "\n\n".join(body)
    return f"{CONVERSION_SUBJECT}\n\n{joined}\n\n{CO_AUTHOR}\n"


def _listed(items: Sequence[str]) -> str:
    """Sentences as a bulleted list, keeping any line breaks of their own.

    An item may be a sentence followed by lines quoted out of a recipe. Those
    are passed through unwrapped, for the same reason `commit_message` gives a
    source URL a line to itself: a line broken to fit a column is a line nobody
    can paste back into the file it came from.
    """
    rendered = []
    for item in items:
        sentence, _, quoted = item.partition("\n")
        rendered.append(
            textwrap.fill(
                sentence,
                WIDTH,
                initial_indent="  - ",
                subsequent_indent="    ",
                break_long_words=False,
                break_on_hyphens=False,
            )
        )
        rendered.extend(f"  {line}" for line in quoted.splitlines())
    return "\n".join(rendered)


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

    def push_migration(
        self,
        pull: BotPullRequest,
        forge_config: str,
        conversion: str,
        conversion_note: str,
        recipe: str,
        recipe_note: str,
    ) -> Pushed:
        """Convert and reconcile ``pull``'s recipe as two commits, then push.

        **Two commits, never one** (DESIGN.md 7.1). The conversion deletes
        `meta.yaml`, adds `recipe.yaml` and edits `conda-forge.yml`, which is
        a diff nobody can read; the reconciliation that follows touches a
        handful of dependency lines in a file that now exists, which is a diff
        somebody can review. Combined, the second disappears inside the first.

        **One clone and one push, because they are one unit.** The second
        commit cannot be made in a second clone: the first push moved the
        branch, so cloning again would find a head that no longer matches what
        was planned against and refuse -- correctly, and uselessly, since the
        commit it disagrees with is swage's own from a moment earlier.
        """
        directory = self._clone(pull)
        (directory / RECIPE_V1).write_text(conversion, encoding="utf-8")
        (directory / CONDA_FORGE_YML).write_text(forge_config, encoding="utf-8")
        (directory / RECIPE_V0).unlink()
        self._git(
            directory, "add", "--all", "--", RECIPE_V0, RECIPE_V1, CONDA_FORGE_YML
        )
        self._git(directory, "commit", "--message", conversion_note)

        (directory / RECIPE_V1).write_text(recipe, encoding="utf-8")
        self._git(directory, "add", "--", RECIPE_V1)
        self._git(directory, "commit", "--message", recipe_note)

        # Never `--force`, the same rule `push_recipe` follows: swage adds
        # commits to somebody's branch and has no business rewriting it.
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
