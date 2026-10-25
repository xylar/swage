"""Tests for `swage apply` (DESIGN.md §12.1).

Against a real git repository in a temporary directory rather than a fake
runner, because the working tree is both what `apply` reads and what it
writes: what is worth pinning is that the commits land on the branch the
checkout has out, that a dry run leaves it as it was, and that nothing is
ever asked of GitHub about the feedstock itself.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from swage.cli import ExitCode, main
from swage.cli.apply import NEVER, run_apply
from swage.cli.pipeline import NameSources
from swage.config import MappingLayer
from swage.forge import (
    COMMIT_SUBJECT,
    CONVERSION_SUBJECT,
    Checkout,
    ForgeError,
    Git,
    NotFound,
    run_gh,
)
from swage.mapping import StaticPackageIndex

from .test_cli_scan import RECIPE, STALE_RECIPE, fetcher
from .test_cli_update import FORGE_YML, META_YAML, tree_at

ORIGIN = "https://github.com/conda-forge/demo-feedstock.git"


@pytest.fixture
def names() -> NameSources:
    return NameSources(
        StaticPackageIndex.of("requests", "pandas", "flit-core"),
        MappingLayer("grayskull pypi mapping", {}),
    )


def git(root: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *argv], check=True, text=True, capture_output=True
    ).stdout.strip()


def checkout_of(
    tmp_path: Path, files: dict[str, str], remotes: Sequence[str] = (ORIGIN,)
) -> Path:
    """A feedstock checkout with ``files`` committed on a branch of its own."""
    root = tmp_path / "demo-feedstock"
    root.mkdir()
    git(root, "init", "--quiet", "--initial-branch", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    for index, url in enumerate(remotes):
        git(root, "remote", "add", "origin" if index == 0 else f"fork{index}", url)
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "--message", "Start")
    git(root, "switch", "--quiet", "--create", "fix")
    return root


def git_only(argv: Sequence[str]) -> str:
    """A runner that runs git and refuses everything else: no read of the
    feedstock may reach GitHub, and this one has nothing upstream there.
    """
    assert argv[0] == "git", f"asked GitHub: {' '.join(argv)}"
    return run_gh(argv)


def apply(root: Path, tree: Any, names: NameSources, write: bool = True) -> Any:
    run = run_apply(
        Checkout.open(root, run=git_only),
        Git(run=git_only),
        tree,
        names,
        write=write,
        fetch=fetcher(),
    )
    return run.feedstocks[0]


def test_the_dependency_update_is_committed_to_the_branch_checked_out(
    tmp_path: Path, names: NameSources
) -> None:
    root = checkout_of(tmp_path, {"recipe/recipe.yaml": STALE_RECIPE})
    start = git(root, "rev-parse", "HEAD")

    record = apply(root, tree_at(tmp_path, "propose"), names)

    assert record.outcome == "committed"
    assert record.head == start
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD") == "fix"
    assert git(root, "log", "--format=%s", f"{start}..") == COMMIT_SUBJECT
    assert (root / "recipe" / "recipe.yaml").read_text() == RECIPE
    assert git(root, "status", "--porcelain") == ""
    assert any(note.startswith("committed to fix: ") for note in record.notes)


def test_a_dry_run_commits_nothing_and_says_what_it_would(
    tmp_path: Path, names: NameSources
) -> None:
    root = checkout_of(tmp_path, {"recipe/recipe.yaml": STALE_RECIPE})
    start = git(root, "rev-parse", "HEAD")

    record = apply(root, tree_at(tmp_path, "propose"), names, write=False)

    assert git(root, "rev-parse", "HEAD") == start
    assert (root / "recipe" / "recipe.yaml").read_text() == STALE_RECIPE
    assert record.outcome == "committed"
    assert "would commit to fix: the dependency update" in record.notes


def test_a_recipe_that_already_matches_gets_no_commit(
    tmp_path: Path, names: NameSources
) -> None:
    root = checkout_of(tmp_path, {"recipe/recipe.yaml": RECIPE})
    start = git(root, "rev-parse", "HEAD")

    record = apply(root, tree_at(tmp_path, "propose"), names)

    assert record.outcome == "unchanged"
    assert git(root, "rev-parse", "HEAD") == start


def test_trust_never_keeps_swage_out_of_a_checkout_too(
    tmp_path: Path, names: NameSources
) -> None:
    """The rung's reasons are about swage editing the recipe, which is what
    a commit here would do.
    """
    root = checkout_of(tmp_path, {"recipe/recipe.yaml": STALE_RECIPE})
    start = git(root, "rev-parse", "HEAD")

    record = apply(root, tree_at(tmp_path, "never"), names)

    assert record.outcome == "needs-review"
    assert record.reason == NEVER
    assert git(root, "rev-parse", "HEAD") == start


def test_a_v0_recipe_is_converted_then_updated_in_two_commits(
    tmp_path: Path, names: NameSources
) -> None:
    """No bot pull request needed, and no version update either: the
    conversion rides along with a dependency fix somebody chose to make.
    """
    root = checkout_of(
        tmp_path, {"recipe/meta.yaml": META_YAML, "conda-forge.yml": FORGE_YML}
    )
    start = git(root, "rev-parse", "HEAD")

    record = apply(root, tree_at(tmp_path, "propose"), names)

    subjects = git(root, "log", "--reverse", "--format=%s", f"{start}..")
    assert subjects.splitlines() == [CONVERSION_SUBJECT, COMMIT_SUBJECT]
    assert not (root / "recipe" / "meta.yaml").exists()
    assert "rattler-build" in (root / "conda-forge.yml").read_text()
    assert record.outcome == "committed"
    assert any(note.startswith("rerender before this merges") for note in record.notes)


def test_uncommitted_changes_are_refused_before_anything_is_read(
    tmp_path: Path,
) -> None:
    root = checkout_of(tmp_path, {"recipe/recipe.yaml": STALE_RECIPE})
    (root / "recipe" / "recipe.yaml").write_text(RECIPE)

    with pytest.raises(ForgeError, match="uncommitted changes"):
        Checkout.open(root, run=git_only)


def test_a_fork_and_conda_forge_name_the_same_feedstock(tmp_path: Path) -> None:
    root = checkout_of(
        tmp_path,
        {"recipe/recipe.yaml": RECIPE},
        remotes=(ORIGIN, "git@github.com:someone/demo-feedstock.git"),
    )

    assert Checkout.open(root, run=git_only).feedstock == "demo"


def test_a_checkout_naming_no_feedstock_is_refused(tmp_path: Path) -> None:
    root = checkout_of(
        tmp_path,
        {"recipe/recipe.yaml": RECIPE},
        remotes=("https://github.com/someone/elsewhere.git",),
    )

    with pytest.raises(ForgeError, match="cannot tell which feedstock"):
        Checkout.open(root, run=git_only)


def test_the_feedstock_is_read_from_disk_and_nothing_else_is(tmp_path: Path) -> None:
    root = checkout_of(tmp_path, {"recipe/recipe.yaml": RECIPE})
    asked: list[list[str]] = []

    def runner(argv: Sequence[str]) -> str:
        if argv[0] == "git":
            return run_gh(argv)
        asked.append(list(argv))
        return '{"encoding": "base64", "content": "aGk="}'

    checkout = Checkout.open(root, run=runner)
    repo = "conda-forge/demo-feedstock"

    assert checkout.file(repo, "recipe/recipe.yaml", checkout.head) == RECIPE
    with pytest.raises(NotFound):
        checkout.file(repo, "recipe/meta.yaml", checkout.head)
    with pytest.raises(NotFound):
        checkout.api(f"repos/{repo}/contents/.ci_support")
    assert asked == []
    assert checkout.file("someone/upstream", "pyproject.toml", "v1") == "hi"
    assert len(asked) == 1


def test_a_checkout_cannot_write_to_github(tmp_path: Path) -> None:
    checkout = Checkout.open(
        checkout_of(tmp_path, {"recipe/recipe.yaml": RECIPE}), run=git_only
    )

    with pytest.raises(ForgeError, match="writes nothing to GitHub"):
        checkout.comment("conda-forge/demo-feedstock", 1, "hello")


def test_the_command_refuses_a_directory_that_is_not_a_checkout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["apply", str(tmp_path)]) == ExitCode.FAILED
    assert "is not a git checkout" in capsys.readouterr().err
