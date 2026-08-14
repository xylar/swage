"""Tests for the Path B merge check (DESIGN.md 5.2).

swage merges a pull request only where it changed nothing, and then it is the
only thing between the bot and `main` -- so what is asserted here is mostly
the *refusals*. Every one of them is a case where a pull request looks
finished from one angle and is not: a build that has not started, a status
posted twice, conda-forge's own automerge job reporting on itself.

The rules being tested are a port of conda-forge's `automerge.py` rather than
swage's own invention, so a test here that disagreed with that file would be
pinning a bug. The fleet sweep in `scripts/` is what checks the port against
the original over every feedstock on disk; these pin the shapes that file
makes hard to see.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from swage.forge import (
    BotPullRequest,
    CheckState,
    GitHub,
    NotFound,
    required_checks,
    resolve_states,
    verify_ci,
)

DISABLED_WORKFLOW = """\
name: Disabled build
on: [push]
jobs:
  build:
    steps:
      - run: exit 0
    if: false
"""

LIVE_WORKFLOW = """\
name: Build conda package
on: [push, pull_request]
jobs:
  build:
    steps:
      - run: build-locally.py
"""

DISABLED_CIRCLE = """\
version: 2
jobs:
  build:
    steps:
      - run: exit 0
workflows:
  all:
    jobs:
      - build:
          filters:
            branches:
              ignore:
                - /.*/
"""

LIVE_CIRCLE = """\
version: 2
jobs:
  build:
    steps:
      - run: build-locally.py
"""


def reader(*paths: str, **contents: str) -> Any:
    """A `Reader` over a handful of paths.

    Positional paths are empty files, which is all most of these rules ask of
    them; the two that are read rather than looked for pass their text.
    """
    tree = {path: "" for path in paths} | {
        path: text for path, text in _named(contents)
    }

    def read(path: str) -> str | None:
        return tree.get(path)

    return read


#: The two files whose *contents* decide anything, under keyword-safe names.
_PATHS = {
    "workflow": ".github/workflows/conda-build.yml",
    "circle": ".circleci/config.yml",
}


def _named(contents: Mapping[str, str]) -> list[tuple[str, str]]:
    return [(_PATHS[name], text) for name, text in contents.items()]


def pull(**rest: Any) -> BotPullRequest:
    return BotPullRequest(
        feedstock="demo",
        number=7,
        title="demo v2.0.0",
        head_sha="abc123",
        head_ref="2.0.0_hbeef",
        head_repo="regro-cf-autotick-bot/demo-feedstock",
        base_ref="main",
        created_at="2026-08-12T00:00:00Z",
        **rest,
    )


def status(context: str, state: str, updated: str = "2026-08-12T00:00:00Z") -> Any:
    return {"context": context, "state": state, "updated_at": updated}


def suite(slug: str, conclusion: str | None, identifier: int = 1) -> Any:
    return {
        "id": identifier,
        "app": {"slug": slug},
        "status": "completed" if conclusion is not None else "in_progress",
        "conclusion": conclusion,
    }


class FakeGitHub:
    """The reads `verify_ci` makes, and nothing else.

    Every path is spelled out rather than pattern-matched, so a call swage was
    not supposed to make fails loudly instead of being answered by a default.
    """

    def __init__(
        self,
        files: Mapping[str, str] | None = None,
        statuses: Sequence[Any] = (),
        suites: Sequence[Any] = (),
        runs: Mapping[int, Sequence[str]] | None = None,
        merged: bool = False,
        mergeable: bool | None = True,
    ) -> None:
        self.files = dict(files or {})
        self.statuses = list(statuses)
        self.suites = list(suites)
        self.runs = dict(runs or {})
        self.merged = merged
        self.mergeable = mergeable
        self.paths: list[str] = []

    def __call__(self, argv: Sequence[str]) -> str:
        assert "--method" in argv and argv[argv.index("--method") + 1] == "GET", (
            "every read must pass --method GET (DESIGN.md 3.5)"
        )
        path = next(part for part in argv if "/" in part and not part.startswith("-"))
        self.paths.append(path)
        if "/contents/" in path:
            return self._contents(path)
        if path.endswith("/statuses"):
            # `--paginate --slurp` answers with a list of pages.
            return json.dumps([self.statuses])
        if path.endswith("/check-suites"):
            return json.dumps({"check_suites": self.suites})
        if path.endswith("/check-runs"):
            identifier = int(path.split("/check-suites/")[1].split("/")[0])
            return json.dumps(
                {"check_runs": [{"name": n} for n in self.runs.get(identifier, ())]}
            )
        if "/pulls/" in path:
            return json.dumps({"merged": self.merged, "mergeable": self.mergeable})
        raise AssertionError(f"unexpected read: {path}")

    def _contents(self, path: str) -> str:
        wanted = path.split("/contents/", 1)[1]
        if wanted not in self.files:
            raise NotFound(f"gh: Not Found (HTTP 404) for {wanted}")
        content = base64.b64encode(self.files[wanted].encode()).decode()
        return json.dumps({"encoding": "base64", "content": content})


def check(github: FakeGitHub, **rest: Any) -> Any:
    return verify_ci(GitHub(run=github), pull(**rest))


def green() -> FakeGitHub:
    """A feedstock with azure configured, and everything passing."""
    return FakeGitHub(
        files={"azure-pipelines.yml": "jobs: []\n"},
        statuses=[
            status("conda-forge-linter", "success"),
            status("conda-forge/azure-pipelines", "success"),
        ],
    )


def test_a_feedstock_with_no_ci_files_still_requires_the_linter() -> None:
    """Which is why an empty required set means something has gone wrong."""
    assert required_checks(reader(), {}) == ("linter",)


def test_each_provider_is_required_by_the_file_conda_smithy_wrote() -> None:
    required = required_checks(
        reader("azure-pipelines.yml", ".travis.yml", ".drone.yml"), {}
    )

    assert set(required) == {"linter", "drone", "travis", "azure"}


def test_a_disabled_github_actions_workflow_is_not_required() -> None:
    """conda-smithy writes the workflow file whether or not it is switched on.

    So the file existing proves nothing, and requiring a check that will never
    be reported would stall every pull request on the feedstock forever.
    """
    disabled = reader(workflow=DISABLED_WORKFLOW)

    assert "github-actions" not in required_checks(disabled, {})
    assert "github-actions" in required_checks(reader(workflow=LIVE_WORKFLOW), {})


def test_circle_is_required_where_its_config_builds_any_branch() -> None:
    assert "circle" not in required_checks(reader(circle=DISABLED_CIRCLE), {})
    assert "circle" in required_checks(reader(circle=LIVE_CIRCLE), {})


def test_a_circle_helper_script_means_circle_is_on() -> None:
    """Either script is conda-smithy's own evidence, ahead of reading config."""
    with_script = reader(".circleci/checkout_merge_commit.sh", circle=DISABLED_CIRCLE)

    assert "circle" in required_checks(with_script, {})


def test_a_feedstock_can_say_which_checks_not_to_wait_for() -> None:
    """`ignored_statuses` is the maintainer's decision, in their own file."""
    config = {"bot": {"automerge_options": {"ignored_statuses": ["azure-pipelines"]}}}

    assert required_checks(reader("azure-pipelines.yml"), config) == ("linter",)


def test_a_required_provider_is_matched_to_its_check_by_substring() -> None:
    """The provider is `azure`; the status context is `conda-forge/azure-pipelines`."""
    states = resolve_states(
        ["azure"], [CheckState("conda-forge/azure-pipelines", True)]
    )

    assert states == (CheckState("azure", True),)


def test_a_provider_that_reported_nothing_at_all_has_not_finished() -> None:
    """Not "passed": a build that has not started is the ordinary state of a
    pull request the bot opened a minute ago, and reading it as a pass would
    merge exactly the pull requests nobody has looked at yet."""
    assert resolve_states(["azure"], []) == (CheckState("azure", None),)


def test_one_failing_build_of_several_fails_the_provider() -> None:
    """Azure reports per platform, so a provider routinely matches four."""
    states = resolve_states(
        ["azure"],
        [
            CheckState("conda-forge/azure-pipelines linux", True),
            CheckState("conda-forge/azure-pipelines win", False),
        ],
    )

    assert states == (CheckState("azure", False),)


def test_a_green_pull_request_is_verified() -> None:
    verified = check(green())

    assert verified.verified
    assert verified.reason == ""
    assert [(state.name, state.word) for state in verified.required] == [
        ("linter", "passed"),
        ("azure", "passed"),
    ]


def test_a_pull_request_whose_ci_has_not_finished_is_pending() -> None:
    """The commonest answer of all, and the reason this is not an exception."""
    github = green()
    github.statuses = [status("conda-forge-linter", "success")]
    waiting = check(github)

    assert not waiting.verified
    assert waiting.pending
    assert "azure" in waiting.reason


def test_a_failing_required_check_is_not_pending() -> None:
    """A human is owed a look now rather than swage coming back later."""
    github = green()
    github.statuses = [
        status("conda-forge-linter", "success"),
        status("conda-forge/azure-pipelines", "failure"),
    ]
    failed = check(github)

    assert not failed.verified
    assert not failed.pending
    assert "azure" in failed.reason


def test_the_newest_status_for_a_context_is_the_one_that_counts() -> None:
    """GitHub keeps every status ever posted, so a re-run leaves two."""
    github = green()
    github.statuses = [
        status("conda-forge/azure-pipelines", "failure", "2026-08-12T00:00:00Z"),
        status("conda-forge/azure-pipelines", "success", "2026-08-12T01:00:00Z"),
        status("conda-forge-linter", "success"),
    ]

    assert check(github).verified


def test_a_failing_check_nobody_required_still_stops_the_merge() -> None:
    """swage's own addition to conda-forge's rule (DESIGN.md 5.2).

    conda-forge asks whether the required checks passed. swage also asks
    whether anything else is broken, because a check nobody made required is
    still somebody's evidence that this build is wrong.
    """
    github = green()
    github.suites = [suite("some-other-app", "failure")]
    refused = check(github)

    assert not refused.verified
    assert "some-other-app" in refused.reason


def test_a_feedstock_may_ignore_a_check_that_is_failing() -> None:
    github = green()
    github.files = dict(
        github.files,
        **{
            "conda-forge.yml": (
                "bot:\n  automerge_options:\n"
                "    ignored_statuses:\n      - some-other-app\n"
            )
        },
    )
    github.suites = [suite("some-other-app", "failure")]

    assert check(github).verified


def test_an_actions_suite_holding_the_automerge_run_is_not_a_passing_build() -> None:
    """conda-forge's rule, and it is not obvious.

    The automerge workflow is itself a GitHub Actions run, so its own suite
    goes green whenever it finishes. Counting it would let a feedstock's
    automerge job stand in for the build that was supposed to have passed.
    """
    github = FakeGitHub(
        files={".github/workflows/conda-build.yml": LIVE_WORKFLOW},
        statuses=[status("conda-forge-linter", "success")],
        suites=[suite("github-actions", "success")],
        runs={1: ["automerge"]},
    )
    refused = check(github)

    assert not refused.verified
    assert "github-actions" in refused.reason


def test_an_actions_suite_holding_the_build_run_is_a_passing_build() -> None:
    github = FakeGitHub(
        files={".github/workflows/conda-build.yml": LIVE_WORKFLOW},
        statuses=[status("conda-forge-linter", "success")],
        suites=[suite("github-actions", "success")],
        runs={1: ["build (linux_64)"]},
    )

    assert check(github).verified


def test_a_pull_request_that_does_not_merge_cleanly_is_refused() -> None:
    github = green()
    github.mergeable = False
    refused = check(github)

    assert not refused.verified
    assert "rebase" in refused.reason


def test_mergeability_github_has_not_computed_yet_is_pending() -> None:
    """GitHub answers null while it works it out, and starts the job on being
    asked. Nothing about the pull request is wrong, so swage comes back."""
    github = green()
    github.mergeable = None
    waiting = check(github)

    assert not waiting.verified
    assert waiting.pending


def test_an_already_merged_pull_request_is_refused() -> None:
    github = green()
    github.merged = True

    assert "already been merged" in check(github).reason


def test_a_draft_pull_request_is_refused_before_anything_is_read() -> None:
    github = green()
    refused = check(github, draft=True)

    assert not refused.verified
    assert github.paths == []


def test_a_feedstock_with_nothing_required_is_refused() -> None:
    """Reachable only by ignoring the linter, and conda-forge refuses it too:
    "every one of zero required checks passed" is the most dangerous sentence
    available."""
    github = green()
    github.files = dict(
        github.files,
        **{
            "conda-forge.yml": (
                "bot:\n  automerge_options:\n"
                "    ignored_statuses:\n      - linter\n      - azure\n"
            )
        },
    )
    refused = check(github)

    assert not refused.verified
    assert "no CI provider" in refused.reason


def test_the_ci_configuration_is_read_from_the_head_of_the_pull_request() -> None:
    """A feedstock that gained a provider in this pull request is judged by
    the commit CI actually ran on, which is the fork's."""
    github = green()
    check(github)

    assert any(
        path.startswith("repos/regro-cf-autotick-bot/demo-feedstock/contents/")
        for path in github.paths
    )


def test_the_feedstock_settings_are_read_from_the_branch_being_merged_into() -> None:
    """conda-forge's reason, kept: a fork can say anything it likes."""
    github = green()
    check(github)

    assert "repos/conda-forge/demo-feedstock/contents/conda-forge.yml" in github.paths


@pytest.mark.parametrize("state", ["error", "failure"])
def test_both_of_githubs_words_for_a_broken_status_are_failures(state: str) -> None:
    github = green()
    github.statuses = [
        status("conda-forge-linter", "success"),
        status("conda-forge/azure-pipelines", state),
    ]

    assert not check(github).verified
