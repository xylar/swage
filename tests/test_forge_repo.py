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
from swage.forge.repo import CO_AUTHOR, conversion_message

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
    """A feedstock's history should explain itself without swage's run record.

    Spelled with the longest names in the fleet rather than with `demo`,
    because that is what went wrong: the prose fits 72 columns for a
    three-character package and runs to 95 for a real one.
    """
    source = "apache/airflow/providers/amazon/pyproject.toml@providers-amazon/9.34.0"
    message = commit_message("apache-airflow-providers-amazon 9.34.0", source)

    lines = message.splitlines()
    assert lines[0] == "Reconcile recipe dependencies with upstream metadata"
    assert lines[1] == ""
    assert "apache-airflow-providers-amazon 9.34.0" in " ".join(lines)
    # On a line of its own and never wrapped: a sdist URL or a monorepo path
    # broken across two lines is one nobody can paste back into anything.
    assert source in lines
    assert max(len(line) for line in lines if line != source) <= 72
    # Never a claim about the gates: a failing gate does not stop the push, so
    # a commit asserting every requirement is attributed would be false on
    # exactly the feedstocks somebody is most likely to read it on.
    assert "attributed" not in message


def prepared_v0(tmp_path: Path, runner: FakeRunner) -> Git:
    """A clone of a v0 feedstock: `meta.yaml` present, `recipe.yaml` not."""
    directory = tmp_path / "demo-7"
    (directory / "recipe").mkdir(parents=True)
    (directory / "recipe" / "meta.yaml").write_text("package:\n  name: demo\n")
    (directory / "conda-forge.yml").write_text("test: native\n")
    return Git(run=runner, root=tmp_path)


def push_a_migration(tmp_path: Path, runner: FakeRunner) -> object:
    return prepared_v0(tmp_path, runner).push_migration(
        pull(),
        forge_config="test: native\nconda_build_tool: rattler-build\n",
        conversion="schema_version: 1\n",
        conversion_note="Convert the recipe to the new format\n",
        recipe="schema_version: 1\n# reconciled\n",
        recipe_note="Reconcile recipe dependencies with upstream metadata\n",
    )


def test_a_migration_is_two_commits_in_one_clone(tmp_path: Path) -> None:
    """Two commits, never one (DESIGN.md 7.1), and never two clones.

    A combined diff deletes `meta.yaml`, adds `recipe.yaml` and rewrites
    `conda-forge.yml`, which buries the dependency edit -- the part that
    actually needs judgment. Splitting them is the whole point.

    They cannot be two clones either: the first push moves the branch, so the
    second clone would find a head that no longer matches what swage planned
    against and refuse -- correctly, over swage's own commit from a moment
    earlier.
    """
    runner = FakeRunner()
    push_a_migration(tmp_path, runner)

    assert runner.verbs == [
        "gh repo clone",
        "rev-parse",
        "add",
        "commit",
        "add",
        "commit",
        "push",
        "rev-parse",
    ]
    assert sum(call[:3] == ["gh", "repo", "clone"] for call in runner.calls) == 1
    assert sum("push" in call for call in runner.calls) == 1


def test_the_conversion_commit_comes_first(tmp_path: Path) -> None:
    """Order is the reviewable part: format, then meaning."""
    runner = FakeRunner()
    push_a_migration(tmp_path, runner)

    messages = [call[-1] for call in runner.calls if "commit" in call]
    assert messages[0].startswith("Convert the recipe to the new format")
    assert messages[1].startswith("Reconcile recipe dependencies")


def test_the_old_recipe_is_deleted_and_staged(tmp_path: Path) -> None:
    """A feedstock left holding both files builds neither predictably."""
    runner = FakeRunner()
    push_a_migration(tmp_path, runner)

    assert not (tmp_path / "demo-7" / "recipe" / "meta.yaml").exists()
    staged = next(call for call in runner.calls if "add" in call)
    assert "recipe/meta.yaml" in staged
    assert "recipe/recipe.yaml" in staged
    assert "conda-forge.yml" in staged


def test_the_second_commit_leaves_the_reconciled_recipe_on_disk(
    tmp_path: Path,
) -> None:
    """What ends up pushed is the reconciled recipe, not the raw conversion."""
    runner = FakeRunner()
    push_a_migration(tmp_path, runner)

    written = (tmp_path / "demo-7" / "recipe" / "recipe.yaml").read_text()
    assert written == "schema_version: 1\n# reconciled\n"


def test_a_migration_never_force_pushes(tmp_path: Path) -> None:
    runner = FakeRunner()
    push_a_migration(tmp_path, runner)

    push = next(call for call in runner.calls if "push" in call)
    assert push[-3:] == ["push", "origin", "HEAD:2.0.0_hbeef"]
    assert "--force" not in push


def test_the_conversion_message_says_why_the_diff_is_unreadable() -> None:
    """A reviewer opening this commit sees a file rewritten end to end.

    Saying so, and saying where the reviewable part is, is the difference
    between a commit that looks broken and one that explains itself.
    """
    message = conversion_message(["conda_build_tool"], [])

    assert message.startswith("Convert the recipe to the new format\n\n")
    assert "conda-recipe-manager" in message
    assert "no useful diff to read" in message
    assert "the commit after this one" in message


def test_the_conversion_message_names_what_was_dropped() -> None:
    """Told up front, a reviewer can look for it.

    Not told, they have to find it in a file that was rewritten end to end,
    which is the one review this commit makes hardest.
    """
    message = conversion_message(
        ["conda_build_tool", "conda_install_tool"],
        ["The variable `tests_to_skip` is defined multiple times."],
    )

    assert "could not carry these over" in message
    assert "tests_to_skip" in message
    assert "conda_build_tool, conda_install_tool" in message


def test_the_conversion_message_leads_with_what_is_wrong_with_the_recipe() -> None:
    """Two headings, because they are two instructions.

    What swage found means the converted recipe is not what the old one said
    and has to be fixed. What the converter reported means somebody should
    look. Merged into one list, the first would be a bullet among several on
    exactly the commits where it is the only thing that matters.
    """
    message = conversion_message(
        [],
        ["Converting `{'if': 'arm64', ...}` is not supported."],
        [
            "the `arm64` condition is not in the converted recipe at all, and "
            "neither is what it applied to:\n"
            "  meta.yaml    - F2C_EXTERNAL_ARITH_HEADER=/arith_arm64.h"
        ],
    )

    wrong = message.index("The conversion is wrong here")
    reported = message.index("The converter could not carry these over")
    assert wrong < reported
    assert "F2C_EXTERNAL_ARITH_HEADER" in message


def test_a_quoted_recipe_line_reaches_the_commit_unwrapped() -> None:
    """The line is the finding, so it is passed through rather than reflowed.

    A build command wrapped to fit a column is one nobody can paste back into
    the file it came from -- and the difference this reports is two characters
    in the middle of such a command, which reflowed prose hides completely.
    The same call `commit_message` makes for a source URL.
    """
    quoted = (
        "recipe.yaml  content: ${{ PYTHON }} -m pip install . -vv "
        "--no-deps --no-build-isolati if unix else '' }}"
    )
    message = conversion_message([], [], [f"the converter cut this short:\n  {quoted}"])

    assert f"\n    {quoted}\n" in message


def test_the_conversion_message_says_what_became_of_each_condition() -> None:
    """The reviewer is reading this on GitHub, where the diff says nothing.

    Every line changed, so on a compiled recipe -- where the conditions are the
    substance -- the ledger is the part of this message with the most in it.
    """
    message = conversion_message(
        ["conda_build_tool"], [], [], ["win      9 lines  ->  9 if:/then: entries"]
    )

    assert "What became of each condition the old recipe stated:" in message
    assert "  win      9 lines  ->  9 if:/then: entries" in message


def test_a_recipe_with_no_conditions_gets_no_ledger() -> None:
    """104 of the fleet's 105 noarch v0 recipes state none at all.

    A heading over an empty list would be two lines of nothing in several
    hundred repositories.
    """
    message = conversion_message(["conda_build_tool"], [])

    assert "What became of each condition" not in message


def test_the_conversion_message_carries_no_design_shorthand() -> None:
    """The worst place for it: a repository swage does not own, permanently.

    swage's first ever pull-request comment said `- **G6**: trust is
    'propose', not 'auto'`, which is exactly the defect (CLAUDE.md).
    """
    message = conversion_message(
        ["conda_build_tool"], ["Could not patch unrecognized license"]
    )

    assert "DESIGN.md" not in message
    assert "path A" not in message and "path B" not in message
    for gate in range(1, 14):
        assert f"G{gate}" not in message


def test_the_conversion_message_wraps_and_is_co_authored() -> None:
    """Same conventions as the reconciliation commit beside it."""
    message = conversion_message([], ["a " * 60])

    assert message.rstrip().endswith(CO_AUTHOR)
    assert all(len(line) <= 72 for line in message.splitlines())


def test_a_clean_conversion_says_only_what_happened() -> None:
    """No settings and no concerns is the common case, and stays short."""
    message = conversion_message([], [])

    assert "conda-forge.yml gains" not in message
    assert "could not carry" not in message
