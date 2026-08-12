"""Tests for discovery and bot pull requests (DESIGN.md 3.4).

The fixtures are shaped from what the live API actually returned for the
maintainer's 487 teams, because three of the things worth testing here are
things the spec gets slightly wrong and only real data shows: a team slug is
not always the feedstock name, not every team is a feedstock, and several open
bot pull requests on one feedstock is ordinary rather than exceptional.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from swage.forge import (
    BotPullRequest,
    ForgeError,
    GitHub,
    discover_feedstocks,
    newest,
    open_bot_pull_requests,
)


class FakeRunner:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> str:
        self.calls.append(list(argv))
        return self.answers.pop(0)


def team(
    name: str, org: str = "conda-forge", slug: str | None = None
) -> dict[str, object]:
    return {
        "name": name,
        "slug": slug or name.replace(".", "-"),
        "organization": {"login": org},
    }


def pull(
    number: int,
    created: str,
    author: str = "regro-cf-autotick-bot",
    title: str = "",
    archived: bool = False,
    labels: Sequence[str] = (),
) -> dict[str, object]:
    return {
        "number": number,
        "title": title or f"demo v{number}",
        "created_at": created,
        "user": {"login": author},
        "head": {"sha": f"{number:040x}", "ref": f"1.0.{number}_hbeef"},
        "base": {"repo": {"archived": archived, "full_name": "conda-forge/demo"}},
        "labels": [{"name": label} for label in labels],
        "draft": False,
    }


def test_the_feedstock_name_is_the_team_name_not_its_slug() -> None:
    """GitHub flattens a dot into a hyphen when it derives a slug.

    Six of the maintainer's 487 teams are affected, and every one has a live
    feedstock under its name and nothing at all under its slug -- so reading
    the slug 404s on exactly the feedstocks whose names nobody would think to
    check.
    """
    runner = FakeRunner(
        json.dumps([[team("proj.4"), team("sqlean.py"), team("smmap")]])
    )
    assert discover_feedstocks(GitHub(run=runner)) == ("proj.4", "smmap", "sqlean.py")


def test_teams_outside_conda_forge_are_not_feedstocks() -> None:
    runner = FakeRunner(
        json.dumps([[team("smmap"), team("something", org="another-org")]])
    )
    assert discover_feedstocks(GitHub(run=runner)) == ("smmap",)


def test_discovery_is_one_paginated_call() -> None:
    """The whole point: ~5 calls where the prior art needed ~600."""
    runner = FakeRunner(json.dumps([[team("smmap")]]))
    discover_feedstocks(GitHub(run=runner))
    assert runner.calls == [
        ["gh", "api", "--method", "GET", "--paginate", "--slurp", "user/teams"]
    ]


def test_every_page_is_flattened() -> None:
    runner = FakeRunner(json.dumps([[team("a")], [team("b")], [team("c")]]))
    assert discover_feedstocks(GitHub(run=runner)) == ("a", "b", "c")


def test_a_read_is_never_sent_as_a_write() -> None:
    """`gh` infers POST from an `-f` field, and POST to /pulls opens one.

    This is not hypothetical -- it is what happened the first time the pull
    listing was called by hand, and GitHub answered `"base", "head" weren't
    supplied`, which is the API declining to create a pull request only
    because the arguments for one were missing.
    """
    runner = FakeRunner(json.dumps([]))
    open_bot_pull_requests(GitHub(run=runner), "demo")
    argv = runner.calls[0]
    assert argv[:4] == ["gh", "api", "--method", "GET"]
    assert "-f" in argv and "state=open" in argv


def test_only_the_bots_pull_requests_are_returned() -> None:
    runner = FakeRunner(
        json.dumps(
            [
                pull(1, "2026-01-01T00:00:00Z"),
                pull(2, "2026-02-01T00:00:00Z", author="xylar"),
            ]
        )
    )
    found = open_bot_pull_requests(GitHub(run=runner), "demo")
    assert [item.number for item in found] == [1]


def test_several_open_bot_pull_requests_are_all_returned_newest_last() -> None:
    """7 of the 15 feedstocks with a bot pull request have more than one."""
    runner = FakeRunner(
        json.dumps(
            [
                pull(103, "2025-08-31T00:00:00Z"),
                pull(105, "2025-09-09T12:00:00Z"),
                pull(104, "2025-09-09T09:00:00Z"),
            ]
        )
    )
    found = open_bot_pull_requests(GitHub(run=runner), "cime_gen_domain")
    assert [item.number for item in found] == [103, 104, 105]
    chosen = newest(found)
    assert chosen is not None
    assert chosen.number == 105


def test_newest_is_none_where_there_is_no_bot_pull_request() -> None:
    assert newest(()) is None


def test_an_archived_feedstock_is_flagged_from_the_pull_request_itself() -> None:
    """Free, and worth having: an archived feedstock cannot be pushed to.

    Four of the maintainer's feedstocks have an open bot pull request that can
    never be merged because the repository is archived.
    """
    runner = FakeRunner(json.dumps([pull(55, "2025-09-03T00:00:00Z", archived=True)]))
    found = open_bot_pull_requests(GitHub(run=runner), "libcf")
    assert found[0].archived is True
    assert found[0].repo == "conda-forge/libcf-feedstock"


def test_labels_come_through_because_automerge_is_one_of_them() -> None:
    runner = FakeRunner(
        json.dumps([pull(55, "2025-09-03T00:00:00Z", labels=["automerge"])])
    )
    assert open_bot_pull_requests(GitHub(run=runner), "libcf")[0].labels == (
        "automerge",
    )


def test_a_listing_that_is_not_a_list_is_refused() -> None:
    runner = FakeRunner(json.dumps({"message": "Not Found"}))
    with pytest.raises(ForgeError, match="not a list"):
        open_bot_pull_requests(GitHub(run=runner), "all-members")


@pytest.mark.parametrize("payload", ["{}", '"text"', "5"])
def test_a_paginated_read_that_is_not_a_list_is_refused(payload: str) -> None:
    runner = FakeRunner(payload)
    with pytest.raises(ForgeError, match="did not return a list"):
        discover_feedstocks(GitHub(run=runner))


def test_the_pull_request_carries_what_reading_the_recipe_needs() -> None:
    runner = FakeRunner(json.dumps([pull(187, "2026-08-01T00:00:00Z")]))
    found = open_bot_pull_requests(GitHub(run=runner), "demo")[0]
    assert isinstance(found, BotPullRequest)
    assert found.head_sha and found.head_ref
    assert found.feedstock == "demo"
