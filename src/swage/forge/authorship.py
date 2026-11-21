"""Which of a recipe's comments another maintainer wrote (DESIGN.md §10, §16).

swage removes no comment another maintainer wrote. Another maintainer is any
author of the commit that last touched the line who is neither the person
running swage -- whether they edited by hand, through swage or through another
tool -- nor one of conda-forge's bots.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .discover import BOT_AUTHORS
from .feedstock import RECIPE_V1
from .github import Author, GitHub

__all__ = ["AGENTS", "BOTS", "PREDECESSORS", "others_comments", "written_by_others"]

#: conda-forge's bots, and `web-flow`: GitHub's own account, which is what a
#: `Co-authored-by` trailer with `noreply@github.com` resolves to -- swage's
#: own trailer among them. A `[bot]` login or name is a bot too.
BOTS = frozenset(
    {*BOT_AUTHORS, "conda-forge-curator", "conda-forge-linter", "web-flow"}
)


#: Accounts that are tools rather than people: `claude` is what an agent's
#: `Co-Authored-By: Claude <noreply@anthropic.com>` trailer resolves to.
AGENTS = frozenset({"claude"})

#: The commit names of the tools swage replaces, which had no GitHub account
#: and committed for whoever ran them -- the same people who run swage, so
#: what they wrote is swage's to rewrite (`authored._RETIRED` is its wording).
PREDECESSORS = frozenset({"airflow-providers-updater"})


def _is_bot(author: Author) -> bool:
    login = author.login or ""
    if author.login is None and author.name in PREDECESSORS:
        return True
    return (
        login in BOTS
        or login in AGENTS
        or login.endswith("[bot]")
        or author.name.endswith("[bot]")
    )


def written_by_others(authors: Sequence[Author], operator: str) -> tuple[str, ...]:
    """The other maintainers among one commit's authors, by login or name.

    The commit's own author always counts, a stranger without a GitHub account
    included, unless it is one of the tools swage replaces. A co-author counts
    only with an account, and not an agent's: a trailer naming no one on
    GitHub is a tool's.
    """
    if not authors:
        return ()
    counted = (authors[0], *(author for author in authors[1:] if author.login))
    return tuple(
        dict.fromkeys(
            author.login or author.name
            for author in counted
            if not _is_bot(author) and author.login != operator
        )
    )


def others_comments(
    github: GitHub, feedstock: str, ref: str, recipe: str
) -> Mapping[str, tuple[str, ...]]:
    """Each whole-line comment in ``recipe`` that another maintainer wrote,
    stripped, with who wrote it. ``recipe`` is the file as it stands at ``ref``.
    """
    if not any(line.lstrip().startswith("#") for line in recipe.splitlines()):
        # Nothing to protect, so nothing to ask.
        return {}
    lines = github.blame(f"conda-forge/{feedstock}-feedstock", RECIPE_V1, ref)
    found: dict[str, dict[str, None]] = {}
    for number, line in enumerate(recipe.splitlines()):
        comment = line.strip()
        if not comment.startswith("#") or number >= len(lines) or not lines[number]:
            continue
        # Asked only once a comment has an author to compare: who is logged
        # in is a read of its own.
        who = written_by_others(lines[number], github.login)
        if who:
            found.setdefault(comment, {}).update(dict.fromkeys(who))
    return {comment: tuple(who) for comment, who in found.items()}
