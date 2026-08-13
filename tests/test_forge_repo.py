"""Tests for the git write path (DESIGN.md 3.5, 5.1).

No network, no `gh` and no real clone: the runner is injected, so what these
assert is the *argv sequence* swage would run. That is the right thing to pin
here, because every hazard in this module is an argument -- pushing to the
feedstock instead of the fork, letting `push.default` choose a refspec, or
committing on top of a branch that has moved since swage read it.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from swage.forge import BotPullRequest, ForgeError, Git, commit_message
from swage.forge.repo import CO_AUTHOR

HEAD = "caf01ea7e0d0cf996fa2e28b224a1977652395fc"


class FakeRunner:
    """Answers `rev-parse` with a SHA and everything else with silence."""

    def __init__(self, head: str = HEAD, fail: str | None = None) -> None:
        self.head = head
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> str:
        self.calls.append(list(argv))
        if self.fail is not None and self.fail in argv:
            raise ForgeError(f"{' '.join(argv)} failed:\nremote rejected")
        if "rev-parse" in argv:
            return f"{self.head}\n"
        return ""

    @property
    def verbs(self) -> list[str]:
        """The interesting token of each call, in order."""
        return [
            call[3] if call[0] == "git" else " ".join(call[:3]) for call in self.calls
        ]


def pull(**rest: object) -> BotPullRequest:
    fields: dict[str, object] = {
        "feedstock": "demo",
        "number": 7,
        "title": "demo v2.0.0",
        "head_sha": HEAD,
        "head_ref": "2.0.0_hbeef",
        "head_repo": "regro-cf-autotick-bot/demo-feedstock",
        "base_ref": "main",
        "created_at": "2026-08-12T00:00:00Z",
    }
    fields.update(rest)
    return BotPullRequest(**fields)  # type: ignore[arg-type]


def prepared(tmp_path: Path, runner: FakeRunner) -> Git:
    """A `Git` whose clone step leaves a directory behind, as a real one does."""
    directory = tmp_path / "demo-7"
    (directory / "recipe").mkdir(parents=True)
    return Git(run=runner, root=tmp_path)


def test_the_clone_targets_the_fork_the_branch_is_actually_on(
    tmp_path: Path,
) -> None:
    """A commit on a pull request belongs to its head repository.

    Cloning `conda-forge/<feedstock>-feedstock` instead would at best fail and
    at worst put swage on the feedstock's own default branch.
    """
    runner = FakeRunner()
    prepared(tmp_path, runner).push_recipe(pull(), "recipe: text\n", "subject\n")

    clone = runner.calls[0]
    assert clone[:4] == ["gh", "repo", "clone", "regro-cf-autotick-bot/demo-feedstock"]
    assert "--branch" in clone and "2.0.0_hbeef" in clone
    assert "--single-branch" in clone and "--depth" in clone


def test_the_push_names_its_refspec_rather_than_relying_on_a_default(
    tmp_path: Path,
) -> None:
    """`push.default = nothing` is a real configuration and this maintainer's.

    An explicit `HEAD:<ref>` behaves the same whatever git is configured to do
    with a bare `git push`, and says out loud which branch is being written.
    """
    runner = FakeRunner()
    prepared(tmp_path, runner).push_recipe(pull(), "recipe: text\n", "subject\n")

    push = next(call for call in runner.calls if "push" in call)
    assert push[-3:] == ["push", "origin", "HEAD:2.0.0_hbeef"]
    assert "--force" not in push


def test_the_recipe_is_written_committed_and_pushed_in_that_order(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    pushed = prepared(tmp_path, runner).push_recipe(
        pull(), "recipe: text\n", "subject\n\nbody\n"
    )

    assert runner.verbs == [
        "gh repo clone",
        "rev-parse",
        "add",
        "commit",
        "push",
        "rev-parse",
    ]
    written = tmp_path / "demo-7" / "recipe" / "recipe.yaml"
    assert written.read_text(encoding="utf-8") == "recipe: text\n"
    assert pushed.sha == HEAD
    assert pushed.path == tmp_path / "demo-7"


def test_the_message_reaches_git_whole(tmp_path: Path) -> None:
    """Passed as one argument, so a body and its trailer survive."""
    runner = FakeRunner()
    message = commit_message("demo 2.0.0", "https://example.invalid/demo.tar.gz")
    prepared(tmp_path, runner).push_recipe(pull(), "recipe: text\n", message)

    commit = next(call for call in runner.calls if "commit" in call)
    assert commit[-2:] == ["--message", message]
    assert message.endswith(f"{CO_AUTHOR}\n")


def test_a_branch_that_moved_since_swage_read_it_is_refused(tmp_path: Path) -> None:
    """The plan was computed against `head_sha`, not against whatever is there.

    A bot push between the read and the clone would leave swage committing a
    recipe reconciled from a base that is no longer the branch.
    """
    runner = FakeRunner(head="0000000000000000000000000000000000000000")

    with pytest.raises(ForgeError, match="but swage planned against"):
        prepared(tmp_path, runner).push_recipe(pull(), "recipe: text\n", "subject\n")

    assert not any("push" in call for call in runner.calls)


def test_a_deleted_fork_is_refused_before_anything_runs(tmp_path: Path) -> None:
    runner = FakeRunner()

    with pytest.raises(ForgeError, match="head repository no longer exists"):
        Git(run=runner, root=tmp_path).push_recipe(
            pull(head_repo=""), "recipe: text\n", "subject\n"
        )

    assert runner.calls == []


def test_a_failed_push_does_not_report_a_commit(tmp_path: Path) -> None:
    """The caller has to hear about this: a commit nobody pushed is not one."""
    runner = FakeRunner(fail="push")

    with pytest.raises(ForgeError, match="remote rejected"):
        prepared(tmp_path, runner).push_recipe(pull(), "recipe: text\n", "subject\n")


def test_the_commit_message_says_which_release_it_read() -> None:
    """A feedstock's history should explain itself without swage's run record."""
    message = commit_message("demo 2.0.0", "https://example.invalid/demo-2.0.0.tar.gz")

    subject, blank, body = message.split("\n", 2)
    assert subject == "Reconcile recipe dependencies with upstream metadata"
    assert blank == ""
    assert "demo 2.0.0" in body
    assert "https://example.invalid/demo-2.0.0.tar.gz" in body
    # Never a claim about the gates: a failing gate does not stop the push, so
    # a commit asserting every requirement is attributed would be false on
    # exactly the feedstocks somebody is most likely to read it on.
    assert "attributed" not in message
