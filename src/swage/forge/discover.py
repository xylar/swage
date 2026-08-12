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

**A feedstock's name is not its package's name**, and `proj.4` is the case in
point: the feedstock is named for what the project used to be called, its
recipe is v1, and the package it builds today is `proj` 9.8.1. Nothing here
infers one name from the other. Everything in this module is addressing a
repository, and the repository is `<feedstock>-feedstock` whatever it builds.

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

from swage.recipe import RecipeError, read_recipe

from .errors import ForgeError, NotFound
from .feedstock import RECIPE_V1
from .github import GitHub

__all__ = [
    "BOT_AUTHORS",
    "BotPullRequest",
    "discover_feedstocks",
    "newest",
    "open_bot_pull_requests",
    "previous_version",
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
    #: What the pull request targets, almost always `main`. Needed to read the
    #: recipe as it stands *without* this pull request, which is what says
    #: whether the version moved and what it moved from.
    base_ref: str
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
    github: GitHub,
    feedstock: str,
    authors: Sequence[str] = BOT_AUTHORS,
    include_archived: bool = False,
) -> tuple[BotPullRequest, ...]:
    """Every open bot pull request on ``feedstock``, newest last.

    All of them rather than one, because several is ordinary rather than
    exceptional: 7 of the 15 feedstocks with a bot pull request have more than
    one open. They come in two shapes, and the report needs to be able to say
    which it acted on. `cime_gen_domain` has four *version bumps* where only
    the newest describes a release anyone wants; `libcf` has four
    *migrations* -- rebuilds for successive Pythons -- which are a different
    thing wearing the same author.

    **Four is not a coincidence.** conda-forge's bot stops filing new pull
    requests once four of its previous ones are sitting unmerged, so a
    feedstock at four is a feedstock where the bot has given up and no further
    version will be offered until somebody clears the backlog. Both examples
    above are at exactly four. That makes the count worth reporting in its own
    right rather than only as context for which one swage picked: it is the
    difference between "three superseded pull requests" and "this feedstock
    has stopped receiving updates".

    **An archived feedstock is dropped**, because it is not swage's business:
    nothing can be pushed to it and nothing can be merged into it, so a pull
    request sitting on one is a pull request no automation should touch. It is
    free to know -- the pull request carries its base repository -- and four of
    the maintainer's feedstocks are in exactly this state, one of them still
    wearing an `automerge` label it will never act on. `include_archived`
    exists so an audit can still see them.
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


def newest(pulls: Sequence[BotPullRequest]) -> BotPullRequest | None:
    """The most recently opened, of whatever is passed in.

    Superseded version bumps pile up: `cime_gen_domain` carries v6.1.120
    through v6.1.123 side by side, and only the newest describes a release
    anyone wants. Filter with `previous_version` first -- this says nothing
    about *what kind* of pull request it is picking.
    """
    return pulls[-1] if pulls else None


def previous_version(
    github: GitHub, pull: BotPullRequest, head_recipe: str
) -> str | None:
    """The version this pull request bumps *from*, or None if it bumps nothing.

    One function answering two questions, because they have the same evidence.
    A pull request is a version update exactly when the recipe's version
    differs from the version on the branch it targets -- and that base version
    is also the one the planner needs to classify a removal as upstream-dropped
    rather than never-upstream (DESIGN.md 3.3.7). Reading it twice for two
    purposes would be reading it twice.

    **swage acts only on version updates.** The bot also files migrations --
    `libcf` has four open, rebuilds for successive Pythons -- and those change
    no version, so there is nothing upstream to reconcile that the recipe does
    not already have. They are a trivial merge when CI is green, and leaving
    them to a human keeps the accountability of somebody having looked and
    judged it safe. Automating them is future work at best, and the risk of
    getting it wrong outweighs what it would save.

    Detected from the version itself rather than from the bot's branch naming.
    `rebuild-*` versus `<version>_<hash>` would work today and would break
    silently the day the bot changes its mind, and a silent break here means
    swage quietly acting on pull requests it was told to leave alone.
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
