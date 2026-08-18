"""Tests for `swage draft` (DESIGN.md 8.1).

The read harness is `test_cli_scan`'s. What matters here is not that files get
written -- that is `test_report_draft` -- but the two decisions the command
makes: which ref it reads a feedstock at, and what a *family* draft says that
running the single-feedstock command fifty times would not.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from swage.cli import ExitCode, main
from swage.cli.consider import NameSources
from swage.cli.draft import run_draft, run_family_draft
from swage.config import MappingLayer, load_config
from swage.forge import ForgeError, GitHub
from swage.mapping import StaticPackageIndex
from swage.plan.gates import GateResult
from swage.report.draft import family_summary, group_questions, render_family

from .conftest import CONFIG_ROOT
from .test_cli_scan import STALE_RECIPE, FakeGitHub, fetcher


class DraftGitHub(FakeGitHub):
    """The scan fake, plus the default branch a draft has to ask for.

    Answers `master`, so a test fails if anything goes back to assuming `main`.
    """

    def __init__(self, branch: str = "master", **rest: Any) -> None:
        super().__init__(**rest)
        self.branch = branch

    def __call__(self, argv: Sequence[str]) -> str:
        path = next(part for part in argv if "/" in part and not part.startswith("-"))
        if re.fullmatch(r"repos/conda-forge/[^/]+-feedstock", path):
            return json.dumps({"default_branch": self.branch})
        return super().__call__(argv)

    def _contents(self, path: str, argv: Sequence[str]) -> str:
        wanted = path.split("/contents/", 1)[1]
        if wanted == ".ci_support":
            return json.dumps([])
        return super()._contents(path, argv)


@pytest.fixture
def names() -> NameSources:
    return NameSources(
        StaticPackageIndex.of("requests", "pandas", "flit-core", "leftover"),
        MappingLayer("grayskull pypi mapping", {}),
    )


@pytest.fixture
def tree(tmp_path: Path) -> Any:
    root = tmp_path / "config"
    shutil.copytree(CONFIG_ROOT, root)
    (root / "feedstocks" / "demo.yaml").write_text(
        "feedstock: demo\ntrust: auto\n", encoding="utf-8"
    )
    return load_config(root)


def gate(name: str, detail: str) -> GateResult:
    return GateResult(name=name, passed=False, detail=detail)


# --- which ref it reads ------------------------------------------------------


def test_a_feedstock_is_drafted_at_its_own_default_branch(
    tmp_path: Path, tree: Any, names: NameSources
) -> None:
    """It used to read `main`, which is silently wrong on a `master` feedstock.

    Wrong in the worst available way: the recipe comes back missing, which
    reads as "this is still v0" -- an answer a maintainer would act on by
    reaching for `swage migrate`.
    """
    runner = DraftGitHub(
        branch="master", pulls=[], files={"recipe/recipe.yaml": STALE_RECIPE}
    )
    workbench, applied = run_draft(
        GitHub(run=runner),
        tree,
        "demo",
        names,
        root=tmp_path / "cache",
        fetch=fetcher(),
    )
    assert applied is None
    assert (workbench.directory / "FINDINGS.md").exists()
    assert any("ref=master" in argv for argv in runner.argvs)


def test_a_v0_feedstock_is_pointed_at_the_command_that_converts_it(
    tmp_path: Path, tree: Any, names: NameSources
) -> None:
    """A draft has nothing to say about a `meta.yaml`, so it says what does.

    The sentence used to end "once it exists", written while `swage migrate`
    was a phase away. It shipped, and the message went on telling maintainers
    to go and do the conversion by hand.
    """
    runner = DraftGitHub(
        pulls=[], files={"recipe/meta.yaml": "package:\n  name: demo\n"}
    )
    with pytest.raises(ForgeError) as refusal:
        run_draft(
            GitHub(run=runner),
            tree,
            "demo",
            names,
            root=tmp_path / "cache",
            fetch=fetcher(),
        )
    assert "swage migrate demo" in str(refusal.value)


# --- what a family draft adds ------------------------------------------------


def test_the_same_question_from_two_feedstocks_is_one_question() -> None:
    """The reason `--family` exists rather than a loop over `draft`.

    Two feedstocks reporting the same sentence about different names are one
    decision, and presenting them as two archaeologies is what makes config
    coverage feel like N pieces of work.
    """
    questions = group_questions(
        {
            "a": [gate("G8", "would remove `google-api-core >=2.17.1,<3.0.0`")],
            "b": [gate("G8", "would remove `google-api-core >=2.24.2,<3.0.0`")],
        }
    )
    assert len(questions) == 1
    assert questions[0].feedstocks == ("a", "b")
    assert len(questions[0].details) == 2, "both wordings are kept"


def test_different_questions_stay_apart() -> None:
    questions = group_questions(
        {
            "a": [gate("G8", "would remove `six`")],
            "b": [gate("G10", "upstream computed `requires-dist` at build time")],
        }
    )
    assert {q.gate for q in questions} == {"G8", "G10"}


def test_the_trust_ladder_is_not_a_question() -> None:
    """It is answered by a `trust` line, not by any archaeology."""
    questions = group_questions(
        {"a": [gate("G6", "not approved for automatic merging (trust: propose)")]}
    )
    assert questions == ()


def test_questions_are_ordered_by_how_many_feedstocks_ask_them() -> None:
    questions = group_questions(
        {
            "a": [gate("G8", "would remove `six`")],
            "b": [gate("G10", "upstream computed `x` at build time")],
            "c": [gate("G10", "upstream computed `y` at build time")],
        }
    )
    assert [q.gate for q in questions] == ["G10", "G8"]


def test_the_summary_says_where_a_shared_answer_belongs() -> None:
    """Where, never what: naming the file is a fact, choosing the value is not."""
    questions = group_questions(
        {name: [gate("G10", "upstream computed `x` at build time")] for name in "abc"}
    )
    summary = family_summary(
        "google-cloud", "config/families/google-cloud.yaml", questions, ["d"], {}
    )
    assert "Asked by 3 feedstocks" in summary
    assert "once for all 3 in `config/families/google-cloud.yaml`" in summary
    assert "a/FINDINGS.md" in summary
    assert "Waiting on nothing" in summary


def test_a_question_only_one_feedstock_asks_points_at_its_own_file() -> None:
    questions = group_questions({"a": [gate("G8", "would remove `six`")]})
    summary = family_summary("fam", "config/families/fam.yaml", questions, [], {})
    assert "config/feedstocks/a.yaml" in summary
    assert "once for all" not in summary


def test_a_feedstock_that_could_not_be_drafted_is_named_with_its_reason() -> None:
    summary = family_summary(
        "fam", "config/families/fam.yaml", (), [], {"a": "no recipe.yaml"}
    )
    assert "Not drafted" in summary
    assert "a  --  no recipe.yaml" in summary


def test_a_family_with_nothing_outstanding_says_so() -> None:
    summary = family_summary("fam", "config/families/fam.yaml", (), ["a", "b"], {})
    assert "Nothing in this family is waiting on a decision" in summary
    assert render_family(Path("/tmp/x"), ()).endswith(
        "nothing in this family is waiting on a decision\n"
    )


def test_a_family_draft_writes_a_summary_and_a_workbench_each(
    tmp_path: Path, tree: Any, names: NameSources
) -> None:
    runner = DraftGitHub(pulls=[], files={"recipe/recipe.yaml": STALE_RECIPE})
    directory, questions = run_family_draft(
        GitHub(run=runner),
        tree,
        "demo-family",
        ["demo"],
        names,
        root=tmp_path / "cache",
        fetch=fetcher(),
    )
    assert (directory / "SUMMARY.md").exists()
    assert (directory / "demo" / "FINDINGS.md").exists()
    assert isinstance(questions, tuple)


def test_one_feedstock_failing_does_not_stop_the_family(
    tmp_path: Path, tree: Any, names: NameSources
) -> None:
    """A sweep that aborts on the first unreadable recipe is unusable."""
    runner = DraftGitHub(pulls=[], files={})
    directory, _ = run_family_draft(
        GitHub(run=runner),
        tree,
        "demo-family",
        ["demo"],
        names,
        root=tmp_path / "cache",
        fetch=fetcher(),
    )
    assert "Not drafted" in (directory / "SUMMARY.md").read_text(encoding="utf-8")


# --- the command -------------------------------------------------------------


def test_execute_is_refused_for_a_family(capsys: pytest.CaptureFixture[str]) -> None:
    """Fifty config files nobody has decided anything about is the failure
    a required `reason` exists to prevent, at family scale."""
    assert main(["draft", "--family", "google-cloud", "--execute"]) == ExitCode.FAILED
    assert "one feedstock at a time" in capsys.readouterr().err


def test_a_bare_draft_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["draft"])
    assert "one of the arguments" in capsys.readouterr().err


def test_a_question_asked_about_one_name_or_two_is_one_question() -> None:
    """The first real family draft reported 41 and 8 where there were 49.

    Removing the fenced names leaves the comma that separated them, so a
    detail listing two names no longer matched one listing a single name --
    the same gate asking the same thing, split by punctuation.
    """
    questions = group_questions(
        {
            "a": [gate("G10", "upstream computed `requires-dist` at build time")],
            "b": [
                gate(
                    "G10",
                    "upstream computed `provides-extra`, `requires-dist` at build time",
                )
            ],
        }
    )
    assert len(questions) == 1
    assert questions[0].feedstocks == ("a", "b")
