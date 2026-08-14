"""Tests for arming automerge (DESIGN.md 2.2, 5.5).

The whole module exists because the obvious spelling is wrong, so the test
that matters is the one asserting a *removal* precedes the add. Everything
else here is about not writing to the wrong repository.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from swage.forge import AUTOMERGE, BotPullRequest, ForgeError, GitHub, arm_automerge


class FakeRunner:
    def __init__(self, *failures: str) -> None:
        self.failures = failures
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> str:
        self.calls.append(list(argv))
        if any(failure in argv for failure in self.failures):
            raise ForgeError(f"{' '.join(argv)} failed:\nHTTP 403")
        return ""


def pull() -> BotPullRequest:
    return BotPullRequest(
        feedstock="demo",
        number=7,
        title="demo v2.0.0",
        head_sha="abc123",
        head_ref="2.0.0_hbeef",
        head_repo="regro-cf-autotick-bot/demo-feedstock",
        base_ref="main",
        created_at="2026-08-12T00:00:00Z",
    )


def test_the_label_is_removed_before_it_is_added() -> None:
    """Re-adding a label that is already there produces no timeline event.

    conda-forge measures "were there commits after the label" against the most
    recent `labeled` event for `automerge`, so a pull request that carried the
    label before swage pushed would still be measured against the old one --
    and the job would strip the label and refuse. Only the removal makes the
    add produce a new event.
    """
    runner = FakeRunner()
    arm_automerge(GitHub(run=runner), pull())

    assert [call[-2:] for call in runner.calls] == [
        ["--remove-label", AUTOMERGE],
        ["--add-label", AUTOMERGE],
    ]


def test_the_label_goes_on_the_feedstock_not_the_fork() -> None:
    """The pull request lives on the base repository; the branch does not."""
    runner = FakeRunner()
    arm_automerge(GitHub(run=runner), pull())

    for call in runner.calls:
        assert call[:4] == ["gh", "pr", "edit", "7"]
        assert call[4:6] == ["--repo", "conda-forge/demo-feedstock"]


def test_a_failure_to_add_the_label_reaches_the_caller() -> None:
    """DEGRADED is the caller's verdict to reach, and it needs to hear (5.5)."""
    runner = FakeRunner("--add-label")

    with pytest.raises(ForgeError, match="HTTP 403"):
        arm_automerge(GitHub(run=runner, max_attempts=1), pull())


def test_a_comment_names_the_repository_it_is_left_on() -> None:
    runner = FakeRunner()
    GitHub(run=runner).comment("conda-forge/demo-feedstock", 7, "body text")

    assert runner.calls == [
        [
            "gh",
            "pr",
            "comment",
            "7",
            "--repo",
            "conda-forge/demo-feedstock",
            "--body",
            "body text",
        ]
    ]


def test_a_transient_failure_labelling_is_retried_before_giving_up() -> None:
    """DESIGN.md 5.5 asks for a retry before a pull request is called DEGRADED."""
    attempts: list[list[str]] = []

    def runner(argv: Sequence[str]) -> str:
        attempts.append(list(argv))
        if len(attempts) < 3:
            raise ForgeError("HTTP 502 Bad Gateway")
        return ""

    GitHub(run=runner, sleep=lambda _: None).label(
        "conda-forge/demo-feedstock", 7, AUTOMERGE
    )

    assert len(attempts) == 3
