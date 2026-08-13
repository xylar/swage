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
    previous_version,
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
        "head": {
            "sha": f"{number:040x}",
            "ref": f"1.0.{number}_hbeef",
            "repo": {"full_name": "regro-cf-autotick-bot/demo-feedstock"},
        },
        "base": {"repo": {"archived": archived, "full_name": "conda-forge/demo"}},
        "labels": [{"name": label} for label in labels],
        "draft": False,
    }


def test_the_feedstock_name_is_the_team_name_not_its_slug() -> None:
    """GitHub flattens a dot into a hyphen when it derives a slug.

    Six of the maintainer's 487 teams are affected, and every one has a live
    feedstock under its name and nothing at all under its slug -- so reading
    the slug 404s on exactly the feedstocks whose names nobody would think to
    check. `proj.4` is one of them, and is not a relic: its recipe is v1 and
    the package it builds is `proj` 9.8.1.
    """
    runner = FakeRunner(
        json.dumps([[team("proj.4"), team("sqlean.py"), team("smmap")]])
    )
    assert discover_feedstocks(GitHub(run=runner)) == ("proj.4", "smmap", "sqlean.py")


def test_the_feedstock_name_is_not_the_package_name() -> None:
    """`proj.4-feedstock` builds `proj`, so nothing may infer one from the other."""
    runner = FakeRunner(json.dumps([[team("proj.4")]]))
    assert discover_feedstocks(GitHub(run=runner)) == ("proj.4",)
    runner = FakeRunner(json.dumps([pull(1, "2026-01-01T00:00:00Z")]))
    found = open_bot_pull_requests(GitHub(run=runner), "proj.4")
    assert found[0].repo == "conda-forge/proj.4-feedstock"


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


def test_an_archived_feedstock_is_not_swages_business() -> None:
    """Nothing can be pushed to it or merged into it, so nothing should touch it.

    Four of the maintainer's feedstocks have an open bot pull request on an
    archived repository, `libcf` among them -- one still wearing an
    `automerge` label that will never act.
    """
    runner = FakeRunner(json.dumps([pull(55, "2025-09-03T00:00:00Z", archived=True)]))
    assert open_bot_pull_requests(GitHub(run=runner), "libcf") == ()


def test_an_audit_can_still_see_the_archived_ones() -> None:
    runner = FakeRunner(json.dumps([pull(55, "2025-09-03T00:00:00Z", archived=True)]))
    found = open_bot_pull_requests(GitHub(run=runner), "libcf", include_archived=True)
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


def test_the_pull_request_carries_the_fork_its_branch_is_on() -> None:
    """Writing to it means writing to the head repository, not the feedstock.

    `repo` is the feedstock everywhere else in swage, so a pull request that
    only carried `feedstock` would make the fork look like somewhere swage
    could derive rather than somewhere it has to be told about.
    """
    runner = FakeRunner(json.dumps([pull(187, "2026-08-01T00:00:00Z")]))
    found = open_bot_pull_requests(GitHub(run=runner), "demo")[0]
    assert found.head_repo == "regro-cf-autotick-bot/demo-feedstock"
    assert found.repo == "conda-forge/demo-feedstock"


def test_a_pull_request_whose_fork_is_gone_has_no_head_repository() -> None:
    """Absence is an answer: there is then no branch anybody can push to."""
    entry = pull(187, "2026-08-01T00:00:00Z")
    entry["head"] = {"sha": "abc", "ref": "1.0.187_hbeef", "repo": None}
    found = open_bot_pull_requests(GitHub(run=FakeRunner(json.dumps([entry]))), "demo")
    assert found[0].head_repo == ""


# A pull request that changes the version, and one that does not. The second
# is what `libcf` has four of: rebuilds for successive Pythons, which move no
# version and which swage is told to leave to a human.
BASE_RECIPE = "context:\n  version: '1.2.3'\nrequirements:\n  run:\n    - python\n"
BUMPED_RECIPE = "context:\n  version: '1.3.0'\nrequirements:\n  run:\n    - python\n"


class RecipeRunner:
    """Answers a contents read for the base branch with a fixed recipe."""

    def __init__(self, base: str | None) -> None:
        self.base = base
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> str:
        self.calls.append(list(argv))
        if self.base is None:
            from swage.forge import NotFound

            raise NotFound("gh: Not Found (HTTP 404)")
        import base64

        content = base64.b64encode(self.base.encode()).decode()
        return json.dumps({"encoding": "base64", "content": content})


def _pull() -> BotPullRequest:
    return BotPullRequest(
        feedstock="demo",
        number=7,
        title="demo v1.3.0",
        head_sha="abc123",
        head_ref="1.3.0_hbeef",
        head_repo="regro-cf-autotick-bot/demo-feedstock",
        base_ref="main",
        created_at="2026-08-12T00:00:00Z",
    )


def test_a_version_bump_reports_the_version_it_bumps_from() -> None:
    """One function, two answers: it is a version update *and* 3.3.7 needs this."""
    runner = RecipeRunner(BASE_RECIPE)
    assert previous_version(GitHub(run=runner), _pull(), BUMPED_RECIPE) == "1.2.3"


def test_a_migration_changes_no_version_and_is_left_alone() -> None:
    """`libcf` has four of these. A trivial merge on green CI, and a human's call.

    Detected from the version rather than from the bot's branch naming, which
    would work today and break in silence the day the bot changes it.
    """
    runner = RecipeRunner(BASE_RECIPE)
    assert previous_version(GitHub(run=runner), _pull(), BASE_RECIPE) is None


def test_a_base_branch_with_no_recipe_is_not_a_version_update() -> None:
    runner = RecipeRunner(None)
    assert previous_version(GitHub(run=runner), _pull(), BUMPED_RECIPE) is None


def test_a_v0_recipe_at_the_head_is_not_a_version_update() -> None:
    """It routes to migration long before this question matters."""
    runner = RecipeRunner(BASE_RECIPE)
    v0 = "{% set version = '1.3.0' %}\npackage:\n  name: demo\n"
    assert previous_version(GitHub(run=runner), _pull(), v0) is None


def test_the_base_branch_is_read_rather_than_the_head() -> None:
    runner = RecipeRunner(BASE_RECIPE)
    previous_version(GitHub(run=runner), _pull(), BUMPED_RECIPE)
    argv = runner.calls[0]
    assert "repos/conda-forge/demo-feedstock/contents/recipe/recipe.yaml" in argv
    assert "ref=main" in argv
