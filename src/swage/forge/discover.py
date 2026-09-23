"""Which feedstocks are mine, and which have a bot pull request (v1 §3.4;
DESIGN.md §10).

Discovery is by org team, one paginated call, and the feedstock name is the
team's `name`, not its `slug`. A feedstock's name is not its package's name. Not
every team is a feedstock: the reader deals with the 404.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from swage.recipe import RecipeError, read_recipe

from .errors import ForgeError, NotFound
from .feedstock import RECIPE_V1
from .github import GitHub

__all__ = [
    "BOT_AUTHORS",
    "BotPullRequest",
    "PullOutcome",
    "discover_feedstocks",
    "newest",
    "open_bot_pull_requests",
    "previous_version",
    "read_pull_request",
]

#: The accounts whose version bumps swage reacts to: the autotick bot and the
#: admin service, which files bumps by request. Missing an author is worse than
#: skipping the feedstock, because swage falls back to the newest bump it does
#: recognize. Both fork to a user account and allow edits by maintainers, so
#: one push path serves both (docs/conda-forge.md; DESIGN.md §16).
BOT_AUTHORS = ("regro-cf-autotick-bot", "conda-forge-admin")

_ORG = "conda-forge"


@dataclass(frozen=True)
class BotPullRequest:
    """An open pull request the version-bump bot has left on a feedstock."""

    feedstock: str
    number: int
    title: str
    head_sha: str
    head_ref: str
    #: The repository the branch lives in, which is a fork: a commit on a pull
    #: request belongs to its head repository (v1 §5.1). Empty where the fork
    #: has been deleted.
    head_repo: str
    #: What the pull request targets, almost always `main`; where the recipe is
    #: read without this pull request.
    base_ref: str
    created_at: str
    labels: tuple[str, ...] = ()
    draft: bool = False
    #: Whether the feedstock is archived, which the pull request carries for
    #: free and which means nothing can be pushed.
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
    github: GitHub,
    feedstock: str,
    authors: Sequence[str] = BOT_AUTHORS,
    include_archived: bool = False,
) -> tuple[BotPullRequest, ...]:
    """Every open bot pull request on ``feedstock``, newest last (v1 §3.4.1).

    All of them, because the report says which it acted on and the count is
    a signal: conda-forge's bot stops filing at four. An archived feedstock
    is dropped unless ``include_archived``, which an audit sets.
    """
    payload = github.api(f"repos/{_ORG}/{feedstock}-feedstock/pulls", {"state": "open"})
    if not isinstance(payload, list):
        raise ForgeError(f"{feedstock}: pull request listing was not a list")
    found = [
        _pull_request(feedstock, entry)
        for entry in payload
        if isinstance(entry, Mapping) and _author(entry) in authors
    ]
    if not include_archived:
        found = [pull for pull in found if not pull.archived]
    return tuple(sorted(found, key=lambda pull: (pull.created_at, pull.number)))


@dataclass(frozen=True)
class PullOutcome:
    """What became of one pull request swage acted on (v1 §8): merged, closed
    or open, since GitHub's `state` alone calls a merged one closed.
    """

    pull: BotPullRequest
    #: `open`, `merged` or `closed`.
    state: str

    @property
    def merged(self) -> bool:
        return self.state == "merged"

    @property
    def open(self) -> bool:
        return self.state == "open"


def read_pull_request(github: GitHub, feedstock: str, number: int) -> PullOutcome:
    """Read one pull request by number, whatever state it is now in. The author
    is not re-checked: a pull request does not change hands.
    """
    payload = github.api(f"repos/{_ORG}/{feedstock}-feedstock/pulls/{number}")
    if not isinstance(payload, Mapping):
        raise ForgeError(f"{feedstock}#{number}: pull request was not an object")
    return PullOutcome(_pull_request(feedstock, payload), _state(payload))


def _state(entry: Mapping[str, Any]) -> str:
    if entry.get("merged") or entry.get("merged_at"):
        return "merged"
    return "closed" if entry.get("state") == "closed" else "open"


def newest(pulls: Sequence[BotPullRequest]) -> BotPullRequest | None:
    """The most recently opened, of whatever is passed in. Filter with
    `previous_version` first.
    """
    return pulls[-1] if pulls else None


def previous_version(
    github: GitHub, pull: BotPullRequest, head_recipe: str
) -> str | None:
    """The version this pull request bumps from, or None if it bumps nothing
    (v1 §3.4.1, §3.3.7).

    swage acts only on version updates; a migration changes no version.
    Detected from the version itself rather than from the bot's branch
    naming.
    """
    head = _recipe_version(head_recipe)
    if head is None:
        return None
    try:
        base = _recipe_version(github.file(pull.repo, RECIPE_V1, pull.base_ref))
    except NotFound:
        # No recipe on the base branch at all. Nothing to compare, so nothing
        # swage can call a version update.
        return None
    return base if base is not None and base != head else None


def _recipe_version(text: str) -> str | None:
    try:
        return read_recipe(text).context.get("version")
    except RecipeError:
        # A v0 recipe, or one swage cannot read. Either way the feedstock is
        # routed elsewhere before this matters.
        return None


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
        head_repo=str(_mapping(head, "repo").get("full_name", "")),
        base_ref=str(_mapping(entry, "base").get("ref", "")),
        created_at=str(entry.get("created_at", "")),
        labels=tuple(
            str(label["name"])
            for label in (labels if isinstance(labels, list) else [])
            if isinstance(label, Mapping) and "name" in label
        ),
        draft=bool(entry.get("draft", False)),
        archived=bool(repo.get("archived", False)),
    )
