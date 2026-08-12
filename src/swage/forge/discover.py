"""Which feedstocks are mine, and which have a bot pull request (DESIGN.md 3.4).

Every conda-forge feedstock has a matching org team whose members are its
maintainers, and team membership is what actually grants the push and merge
access swage needs. So the authoritative, cheap answer to "which feedstocks do
I maintain" is one paginated call, where the google-cloud tool's approach --
search every repo, fetch every recipe, check `recipe-maintainers` -- costs
around 600.

**The feedstock name is the team's `name`, not its `slug`.** GitHub flattens a
dot to a hyphen when it derives a slug, so `proj.4` becomes `proj-4` and
`sqlean.py` becomes `sqlean-py`. Six of the maintainer's 487 teams are
affected, and every one of them has a live feedstock under its *name* and
nothing at all under its slug -- so reading the slug 404s on exactly the
feedstocks whose names are unusual enough that nobody would notice the gap.

**Not every team is a feedstock.** `all-members` is an org-wide team and has
no repository behind it. There is no way to tell from the team alone, so
discovery reports what it found and the reader deals with a feedstock that
turns out not to exist -- one 404 in 487, against a hardcoded exclusion list
that would go stale silently.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .errors import ForgeError
from .github import GitHub

__all__ = [
    "BOT_AUTHORS",
    "BotPullRequest",
    "discover_feedstocks",
    "newest",
    "open_bot_pull_requests",
]

#: The bot swage reacts to. A list because conda-forge's bot has appeared
#: under more than one account name over the years, and a feedstock whose
#: pull request swage does not recognize is a feedstock swage silently skips.
BOT_AUTHORS = ("regro-cf-autotick-bot",)

_ORG = "conda-forge"


@dataclass(frozen=True)
class BotPullRequest:
    """An open pull request the version-bump bot has left on a feedstock."""

    feedstock: str
    number: int
    title: str
    head_sha: str
    head_ref: str
    created_at: str
    labels: tuple[str, ...] = ()
    draft: bool = False
    #: Whether the *feedstock* is archived. Free here -- the pull request
    #: carries its base repository -- and worth having, because an archived
    #: feedstock cannot be pushed to at all. Four of the maintainer's have an
    #: open bot pull request that can never be merged.
    archived: bool = False

    @property
    def repo(self) -> str:
        return f"{_ORG}/{self.feedstock}-feedstock"


def discover_feedstocks(github: GitHub) -> tuple[str, ...]:
    """Every conda-forge feedstock the authenticated user maintains."""
    teams = github.paginated("user/teams")
    names = [
        team["name"]
        for team in teams
        if isinstance(team, Mapping)
        and isinstance(team.get("name"), str)
        and _organization(team) == _ORG
    ]
    return tuple(sorted(set(names)))


def _organization(team: Mapping[str, Any]) -> str | None:
    organization = team.get("organization")
    if isinstance(organization, Mapping):
        login = organization.get("login")
        return login if isinstance(login, str) else None
    return None


def open_bot_pull_requests(
    github: GitHub, feedstock: str, authors: Sequence[str] = BOT_AUTHORS
) -> tuple[BotPullRequest, ...]:
    """Every open bot pull request on ``feedstock``, newest last.

    All of them rather than one, because several is ordinary rather than
    exceptional: 7 of the 15 feedstocks with a bot pull request have more than
    one open. They come in two shapes, and the report needs to be able to say
    which it acted on. `cime_gen_domain` has four *version bumps* where only
    the newest is live and the rest are superseded; `libcf` has four
    *migrations* -- rebuilds for successive Pythons -- which are a different
    thing wearing the same author.
    """
    payload = github.api(f"repos/{_ORG}/{feedstock}-feedstock/pulls", {"state": "open"})
    if not isinstance(payload, list):
        raise ForgeError(f"{feedstock}: pull request listing was not a list")
    found = [
        _pull_request(feedstock, entry)
        for entry in payload
        if isinstance(entry, Mapping) and _author(entry) in authors
    ]
    return tuple(sorted(found, key=lambda pull: (pull.created_at, pull.number)))


def newest(pulls: Sequence[BotPullRequest]) -> BotPullRequest | None:
    """The one swage acts on where a feedstock has several.

    The most recently opened, because whatever the bot filed last is its
    current view: `cime_gen_domain`'s v6.1.120 through v6.1.122 are superseded
    by v6.1.123 sitting beside them. This is a policy rather than a discovery,
    so the caller reports how many there were instead of quietly acting on one
    of four.
    """
    return pulls[-1] if pulls else None


def _author(entry: Mapping[str, Any]) -> str | None:
    user = entry.get("user")
    if isinstance(user, Mapping):
        login = user.get("login")
        return login if isinstance(login, str) else None
    return None


def _mapping(node: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """A nested object, or an empty one where the API sent something else."""
    value = node.get(key)
    return value if isinstance(value, Mapping) else {}


def _pull_request(feedstock: str, entry: Mapping[str, Any]) -> BotPullRequest:
    head = _mapping(entry, "head")
    repo = _mapping(_mapping(entry, "base"), "repo")
    labels = entry.get("labels")
    return BotPullRequest(
        feedstock=feedstock,
        number=int(entry.get("number", 0)),
        title=str(entry.get("title", "")),
        head_sha=str(head.get("sha", "")),
        head_ref=str(head.get("ref", "")),
        created_at=str(entry.get("created_at", "")),
        labels=tuple(
            str(label["name"])
            for label in (labels if isinstance(labels, list) else [])
            if isinstance(label, Mapping) and "name" in label
        ),
        draft=bool(entry.get("draft", False)),
        archived=bool(repo.get("archived", False)),
    )
