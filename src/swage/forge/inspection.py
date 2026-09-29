"""Switch the bot from `update-grayskull` to `hint-grayskull` (DESIGN.md
§5.3, §9.8).

Under `update-grayskull` the autotick bot rewrites the requirement lines swage
reconciles, from grayskull's reading of the release; under `hint-grayskull` it
lists the same differences in its pull request's description. The switch is
one line of `conda-forge.yml`, edited as text so nothing else in the file
moves, and checked by parsing the file on both sides of the edit.

A person who puts the line back has decided, and swage does not argue: the
file's own history says whether swage has made the switch before.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import yaml

from .checks import CONDA_FORGE_YML
from .discover import BotPullRequest
from .errors import NotFound
from .github import GitHub

__all__ = [
    "HINT",
    "SWITCH_SUBJECT",
    "UPDATE",
    "Inspection",
    "plan_inspection",
    "switched",
]

UPDATE = "update-grayskull"
HINT = "hint-grayskull"

#: The subject of the commit that makes the switch, fixed so the file's
#: history can be searched for it.
SWITCH_SUBJECT = f"Switch the bot from {UPDATE} to {HINT}"

#: The one line the switch edits. Anything else saying `update-grayskull` is
#: left for a person.
_LINE = re.compile(
    rf"^(?P<lead>[ \t]+inspection:[ \t]*)['\"]?{UPDATE}['\"]?(?P<tail>[ \t]*(?:#.*)?)$",
    re.MULTILINE,
)

#: Said where the file says `update-grayskull` in a shape swage does not edit.
UNEDITABLE = (
    f"{CONDA_FORGE_YML} says {UPDATE} in a form swage does not edit -- "
    f"switch it to {HINT} by hand"
)


def reverted(sha: str) -> str:
    """Said where a person has put back what swage switched."""
    return (
        f"{CONDA_FORGE_YML} is back on {UPDATE} since swage switched it in "
        f"{sha[:7]}, so swage leaves it there"
    )


@dataclass(frozen=True)
class Inspection:
    """What becomes of `conda-forge.yml` on one pull request."""

    #: The file as swage would write it, or None where it writes nothing.
    text: str | None = None
    #: Why a file saying `update-grayskull` is left as it is.
    note: str = ""


def switched(text: str) -> str | None:
    """``text`` with `bot.inspection` switched to `hint-grayskull`, or None
    where it does not say `update-grayskull` there.

    Raises ValueError where it does, in a shape the one-line edit cannot be
    shown to change and nothing else.
    """
    if _inspection(text) != UPDATE:
        return None
    matches = list(_LINE.finditer(text))
    if len(matches) != 1:
        raise ValueError(UNEDITABLE)
    match = matches[0]
    edited = (
        f"{text[: match.start()]}{match['lead']}{HINT}{match['tail']}"
        f"{text[match.end() :]}"
    )
    before = yaml.safe_load(text)
    after = yaml.safe_load(edited)
    before["bot"]["inspection"] = HINT
    if after != before:
        raise ValueError(UNEDITABLE)
    return edited


def plan_inspection(
    github: GitHub, pull: BotPullRequest, text: str | None = None
) -> Inspection:
    """Whether to switch ``pull``'s `conda-forge.yml`, and to what.

    ``text`` is the file where swage is already rewriting it, which is a
    conversion (v1 §7.1); otherwise it is read at the pull request's head.
    """
    if text is None:
        try:
            text = github.file(pull.repo, CONDA_FORGE_YML, pull.head_sha)
        except NotFound:
            return Inspection()
    try:
        edited = switched(text)
    except ValueError as exc:
        return Inspection(note=str(exc))
    if edited is None:
        return Inspection()
    earlier = _earlier_switch(github, pull)
    if earlier is not None:
        return Inspection(note=reverted(earlier))
    return Inspection(text=edited)


def _inspection(text: str) -> Any:
    """`bot.inspection`, or None where the file does not say."""
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    bot = loaded.get("bot") if isinstance(loaded, Mapping) else None
    return bot.get("inspection") if isinstance(bot, Mapping) else None


def _earlier_switch(github: GitHub, pull: BotPullRequest) -> str | None:
    """The commit in which swage switched this file before, if it did.

    Read at the pull request's head, whose history is the default branch's
    plus the bot's. A switch on a pull request that closed unmerged is not
    in it, and is not one anybody undid.
    """
    history = github.paginated(
        f"repos/{pull.repo}/commits",
        {"path": CONDA_FORGE_YML, "sha": pull.head_sha},
    )
    for entry in history:
        commit = entry.get("commit") if isinstance(entry, Mapping) else None
        message = commit.get("message") if isinstance(commit, Mapping) else None
        if isinstance(message, str) and message.startswith(SWITCH_SUBJECT):
            return str(entry.get("sha", ""))
    return None
