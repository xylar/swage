"""Clone a pull request's branch and push a commit to it (v1 §3.5, §5.1;
DESIGN.md §10).

The branch is on the bot's fork, so the clone target is `pull.head_repo` and the
push names `pull.head_ref` explicitly. The clone is per run and never reused,
and its head is checked against the SHA the plan was computed from. Nothing here
decides whether to push (v1 §5.4).
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

#: The subject swage writes on every recipe commit, fixed so it can be searched
#: for across several hundred repositories.
COMMIT_SUBJECT = "Reconcile recipe dependencies with upstream metadata"

#: The subject on the conversion commit, fixed for the same reason, in words a
#: feedstock's readers know.
CONVERSION_SUBJECT = "Convert the recipe to the new format"

#: swage claims co-authorship rather than authorship: the commit is authored by
#: whoever ran swage, and a feedstock's `git log` still says which commits a
#: tool wrote.
CO_AUTHOR = "Co-Authored-By: swage <noreply@github.com>"

#: Where git conventionally wraps a commit body. The metadata source is not
#: wrapped, because a URL broken across lines is one nobody can paste.
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


def commit_message(release: str, source: str, moved: Sequence[str] = ()) -> str:
    """The commit swage writes to a feedstock, whole (DESIGN.md §3.1).

    The body says which release was read and out of which file, and nothing
    about the findings, which go in the comment. The source gets a line to
    itself because it cannot be wrapped. ``moved`` is what a source-version
    correction changed (v1 §3.6.5), one line each.
    """
    lead = textwrap.fill(
        f"Written by swage from {release}, whose metadata was read from:",
        WIDTH,
        break_long_words=False,
        break_on_hyphens=False,
    )
    body = [f"{lead}\n{source}"]
    if moved:
        body.append(
            "Also moves a source version the bot does not bump:\n" + _listed(moved)
        )
    joined = "\n\n".join(body)
    return f"{COMMIT_SUBJECT}\n\n{joined}\n\n{CO_AUTHOR}\n"


def conversion_message(
    settings: Sequence[str],
    concerns: Sequence[str],
    damage: Sequence[str] = (),
    conditions: Sequence[str] = (),
) -> str:
    """The commit that converts a recipe, whole (v1 §7.1; DESIGN.md §3.1).

    Separate from the reconciliation commit, so the dependency edit is
    visible. The body names the tool, what else changed, what swage found
    wrong, what the converter could not carry, and the ledger of conditions;
    ``damage`` comes first and keeps its own line breaks.
    """
    body = [
        textwrap.fill(
            "Converted from the old recipe format by conda-recipe-manager. "
            "The whole file is rewritten, so there is no useful diff to read "
            "here. The dependency changes are in the commit after this one, "
            "where they can be reviewed line by line.",
            WIDTH,
        )
    ]
    if settings:
        body.append(
            textwrap.fill(
                "conda-forge.yml gains "
                + ", ".join(settings)
                + "; without them conda-forge would go on building this "
                "feedstock the old way.",
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
    """Sentences as a bulleted list, keeping any line breaks of their own: the
    lines quoted out of a recipe are never wrapped.
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
        """Put ``recipe`` on ``pull``'s branch as one commit, and push it."""
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
        """Convert and reconcile ``pull``'s recipe as two commits, then push
        (v1 §7.1). One clone and one push: the first push moved the branch.
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
        # Through `gh` rather than `git clone`, so the remote is built with the
        # maintainer's own protocol and credentials.
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
        """One git call, addressed by `-C` so the runner needs no cwd, which is what
        lets git and `gh` share `Runner`.
        """
        return self._run(["git", "-C", str(directory), *argv])
