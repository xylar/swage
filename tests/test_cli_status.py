"""Tests for `swage status` (DESIGN.md 8).

The read harness is `test_cli_scan`'s, so a difference between a status run and
a scan of the same pull request is a real difference rather than a difference
in what the two were handed.

Two properties carry the weight here. **Every call this command makes is a
read** -- the phase it belongs to once ended in re-arming a label, and the
reason it does not is that a label added after CI has finished summons nothing
(DESIGN.md 2.1). And **a pull request still open is re-planned rather than
remembered**, because `READY TO MERGE` is a claim about the recipe now.
"""

from __future__ import annotations

import importlib
import json
import shutil
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from swage.cli import ExitCode, main
from swage.cli.consider import NameSources
from swage.cli.status import (
    OVERTAKEN,
    STATUS_DESCRIPTIONS,
    Followed,
    followed,
    parse_since,
    read_runs,
    run_status,
)
from swage.config import MappingLayer, load_config
from swage.forge import GitHub, NotFound
from swage.mapping import StaticPackageIndex
from swage.report import (
    FeedstockRecord,
    RunRecord,
    render_summary,
    run_directory,
    runs_since,
    write_run,
)

from .conftest import CONFIG_ROOT
from .test_cli_scan import (
    BASE_RECIPE,
    GREEN,
    PREVIOUS_SDIST,
    STALE_RECIPE,
    FakeGitHub,
    fetcher,
    pull,
)

CLI = importlib.import_module("swage.cli.main")


class FollowingGitHub(FakeGitHub):
    """`test_cli_scan`'s fake, plus what became of the pull request by number.

    The base fake answers `pulls/<n>` with the mergeability the merge check
    wants. `status` asks the same endpoint a different question, so the state
    is what this adds.
    """

    def __init__(self, state: str = "open", **rest: Any) -> None:
        super().__init__(**rest)
        self.state = state

    def __call__(self, argv: Sequence[str]) -> str:
        path = next(part for part in argv if "/" in part and not part.startswith("-"))
        if "/pulls/" in path and not path.endswith(("/statuses", "/check-suites")):
            return json.dumps(
                {
                    **json.loads(super().__call__(argv)),
                    "state": "open" if self.state == "open" else "closed",
                    "merged": self.state == "merged",
                    **pull(7),
                }
            )
        return super().__call__(argv)


@pytest.fixture
def names() -> NameSources:
    return NameSources(
        StaticPackageIndex.of("requests", "pandas", "flit-core", "leftover"),
        MappingLayer("grayskull pypi mapping", {}),
    )


@pytest.fixture
def tree(tmp_path: Path) -> Any:
    """The shipped quirks database, with `demo` blessed for merging."""
    root = tmp_path / "config"
    shutil.copytree(CONFIG_ROOT, root)
    (root / "feedstocks" / "demo.yaml").write_text(
        "feedstock: demo\ntrust: auto\n", encoding="utf-8"
    )
    return load_config(root)


def record(outcome: str, number: int = 7, pushed: str = "", **rest: Any) -> Any:
    return FeedstockRecord(
        feedstock=rest.pop("feedstock", "demo"),
        outcome=outcome,
        pull_request=number,
        pushed=pushed,
        **rest,
    )


def run(*records: Any) -> RunRecord:
    return RunRecord(command="swage update", started="", feedstocks=records)


# --- the window ------------------------------------------------------------


def test_a_window_is_read_as_days_or_hours() -> None:
    assert parse_since("7d") == timedelta(days=7)
    assert parse_since("36h") == timedelta(hours=36)
    assert parse_since(" 1d ") == timedelta(days=1)


@pytest.mark.parametrize("text", ["7", "d", "90m", "-1d", "7 days", ""])
def test_a_window_that_is_not_one_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match="not a window"):
        parse_since(text)


def test_only_runs_inside_the_window_are_read(tmp_path: Path) -> None:
    now = datetime(2026, 8, 15, 12, tzinfo=UTC)
    for when in (now - timedelta(days=10), now - timedelta(days=1)):
        write_run(run(), run_directory(when, root=tmp_path))
    found = runs_since(now - timedelta(days=7), root=tmp_path)
    assert [directory.name for directory in found] == ["2026-08-14T12-00-00"]


def test_a_directory_that_is_not_a_run_is_passed_over(tmp_path: Path) -> None:
    """Something else living under `runs/` is not an answer to the question."""
    (tmp_path / "runs" / "notes").mkdir(parents=True)
    (tmp_path / "runs" / "notes" / "run.json").write_text("{}", encoding="utf-8")
    when = datetime(2026, 8, 15, 12, tzinfo=UTC)
    write_run(run(), run_directory(when, root=tmp_path))
    found = runs_since(when - timedelta(days=1), root=tmp_path)
    assert [directory.name for directory in found] == ["2026-08-15T12-00-00"]


def test_runs_this_swage_cannot_read_are_counted_rather_than_listed(
    tmp_path: Path,
) -> None:
    """A developing machine held 48 of them, all inside a default window.

    Silence would be worse -- a window covering less than it claims is how a
    report comes back clean by having looked at less -- but a line each would
    bury the report under its own preamble, and the reason is the same for
    every one of them.
    """
    directories = []
    for hour in range(3):
        directory = run_directory(
            datetime(2026, 8, 15, hour, tzinfo=UTC), root=tmp_path
        )
        directory.mkdir(parents=True)
        (directory / "run.json").write_text(json.dumps({"schema": 1}), encoding="utf-8")
        directories.append(directory)
    records, skipped = read_runs(directories)
    assert records == ()
    assert skipped == 3


# --- which pull requests get followed --------------------------------------


def test_a_pull_request_swage_pushed_to_is_followed() -> None:
    assert followed([run(record("merge-ready", pushed="abc1234"))]) == (
        Followed("demo", 7),
    )


def test_a_pull_request_left_waiting_is_followed_without_a_push() -> None:
    """Path B writes nothing, so `pushed` is empty and the question stands."""
    assert followed([run(record("awaiting-ci"))]) == (Followed("demo", 7),)
    assert followed([run(record("ready-to-merge"))]) == (Followed("demo", 7),)


@pytest.mark.parametrize(
    "outcome", ["unchanged", "needs-migration", "failed", "needs-review"]
)
def test_a_pull_request_nothing_happened_to_is_not_followed(outcome: str) -> None:
    """swage neither wrote to it nor is waiting on it, so there is no question."""
    assert followed([run(record(outcome))]) == ()


def test_a_needs_review_pull_request_that_was_pushed_to_is_followed() -> None:
    """The gates held it, but swage's commit is on it and its fate is real."""
    assert followed([run(record("needs-review", pushed="abc1234"))]) == (
        Followed("demo", 7),
    )


def test_the_same_pull_request_in_two_runs_is_asked_about_once() -> None:
    runs = [run(record("merge-ready", pushed="a")), run(record("awaiting-ci"))]
    assert followed(runs) == (Followed("demo", 7),)


def test_two_pull_requests_on_one_feedstock_are_both_asked_about() -> None:
    """Collapsing them would drop the one swage actually pushed to."""
    runs = [
        run(record("merge-ready", number=7, pushed="a")),
        run(record("awaiting-ci", number=9)),
    ]
    assert followed(runs) == (Followed("demo", 7), Followed("demo", 9))


# --- what became of it -----------------------------------------------------


def follow(
    runner: FollowingGitHub, tree: Any, names: NameSources, *records: Any
) -> Any:
    result = run_status(
        GitHub(run=runner), tree, [run(*records)], names, fetch=fetcher()
    )
    return result.feedstocks[0]


def test_a_merged_pull_request_is_the_loop_closing(
    tree: Any, names: NameSources
) -> None:
    runner = FollowingGitHub(state="merged", pulls=[pull(7)])
    found = follow(runner, tree, names, record("merge-ready", pushed="abc1234"))
    assert found.outcome == "merged"
    assert found.detail == "merged since the run that acted on it"


def test_a_pull_request_closed_without_merging_says_the_work_was_not_taken(
    tree: Any, names: NameSources
) -> None:
    runner = FollowingGitHub(state="closed", pulls=[pull(7)])
    found = follow(runner, tree, names, record("merge-ready", pushed="abc1234"))
    assert found.outcome == "closed"
    assert "not taken" in found.detail


def test_a_pull_request_still_open_is_replanned_rather_than_remembered(
    tree: Any, names: NameSources
) -> None:
    """The recipe is stale now, whatever the earlier run recorded about it."""
    runner = FollowingGitHub(
        pulls=[pull(7)], files={"recipe/recipe.yaml": STALE_RECIPE}
    )
    found = follow(runner, tree, names, record("awaiting-ci"))
    assert found.outcome == "merge-ready"
    assert found.sections, "a re-planned pull request carries its plan"


def test_green_ci_turns_a_waiting_pull_request_into_a_ready_one(
    tree: Any, names: NameSources
) -> None:
    """Exactly what the abandoned re-arm was for, and it needs no write.

    The earlier run left this `AWAITING CI` because the recipe already matched
    upstream and the builds had not reported. They have now, so the pull
    request needs no change, is green, and is one click from merged -- which
    is the whole of what re-arming its label would have bought, in the case
    where re-arming could not have worked (DESIGN.md 2.1).
    """
    runner = FollowingGitHub(pulls=[pull(7)], statuses=GREEN)
    found = run_status(
        GitHub(run=runner),
        tree,
        [run(record("awaiting-ci"))],
        names,
        fetch=fetcher(previous=PREVIOUS_SDIST),
    ).feedstocks[0]
    assert found.outcome == "ready-to-merge"
    assert found.merge_check is not None and found.merge_check.verified
    assert found.detail == "CI passed: linter"


def test_a_pull_request_the_base_branch_has_caught_up_with_wants_closing(
    tree: Any, names: NameSources
) -> None:
    """It bumps nothing now, so nothing is left for it to do and none will close it.

    The head recipe carries the version the branch it targets already has,
    which is what a pull request looks like once the change it proposed has
    landed by some other route.
    """
    runner = FollowingGitHub(pulls=[pull(7)], files={"recipe/recipe.yaml": BASE_RECIPE})
    found = follow(runner, tree, names, record("awaiting-ci"))
    assert found.outcome == "needs-review"
    assert found.detail == OVERTAKEN


def test_a_pull_request_that_is_no_longer_there_is_reported_as_such(
    tree: Any, names: NameSources
) -> None:
    """A renamed or deleted feedstock, under a pull request swage pushed to."""

    class Gone(FollowingGitHub):
        def __call__(self, argv: Sequence[str]) -> str:
            path = next(
                part for part in argv if "/" in part and not part.startswith("-")
            )
            if "/pulls/" in path:
                raise NotFound("gh: Not Found (HTTP 404)")
            return super().__call__(argv)

    found = follow(Gone(), tree, names, record("merge-ready", pushed="abc1234"))
    assert found.outcome == "failed"
    assert found.detail == "the pull request is no longer there"


# --- it writes nothing -----------------------------------------------------


def test_every_call_a_status_run_makes_is_a_read(tree: Any, names: NameSources) -> None:
    """The structural claim of the whole command (DESIGN.md 2.1, 5.2.2).

    Re-arming a `DEGRADED` pull request was this phase's original job and it
    cannot work: conda-forge dispatches automerge from CI status events, so a
    label added once CI has finished summons nothing. There is therefore no
    write path here to get wrong, and this is what says so.
    """
    runner = FollowingGitHub(
        pulls=[pull(7)], files={"recipe/recipe.yaml": STALE_RECIPE}
    )
    run_status(
        GitHub(run=runner),
        tree,
        [run(record("degraded", pushed="abc1234"))],
        names,
        fetch=fetcher(),
    )
    assert runner.argvs, "the run made no calls at all"
    for argv in runner.argvs:
        assert argv[:3] == ["gh", "api", "--method"], argv
        assert argv[3] == "GET", argv


# --- the command ------------------------------------------------------------


def test_an_empty_window_says_so_rather_than_printing_a_clean_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty summary would read as "everything landed"."""
    monkeypatch.setattr(CLI, "runs_since", lambda cutoff: ())
    monkeypatch.setenv("SWAGE_CONFIG_ROOT", str(CONFIG_ROOT))
    assert main(["status"]) == ExitCode.OK
    assert "no runs in the last 7d" in capsys.readouterr().out


def test_a_window_that_is_not_one_fails_before_anything_is_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SWAGE_CONFIG_ROOT", str(CONFIG_ROOT))
    assert main(["status", "--since", "soon"]) == ExitCode.FAILED
    assert "not a window" in capsys.readouterr().err


def test_the_report_does_not_claim_to_have_pushed_anything() -> None:
    """`status` reaches the write buckets through the same gates and writes not."""
    rendered = render_summary(
        run(record("merge-ready", pushed="abc1234")),
        descriptions=STATUS_DESCRIPTIONS,
        color=False,
    )
    assert "pushed + labeled automerge" not in rendered
    assert "`swage update` to push again" in rendered


def test_a_window_whose_runs_left_nothing_in_flight_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The commonest morning of all, and an empty report would look broken.

    A read-only sweep of the whole fleet pushes to nothing and leaves nothing
    waiting, so `status` after one has no question to ask -- which is good
    news, and has to read as good news rather than as a summary that failed
    to render.
    """
    monkeypatch.setattr(
        CLI, "read_runs", lambda directories: ((run(record("unchanged")),), 0)
    )
    monkeypatch.setattr(CLI, "runs_since", lambda cutoff: (Path("run"),))
    monkeypatch.setenv("SWAGE_CONFIG_ROOT", str(CONFIG_ROOT))
    assert main(["status", "--since", "1d"]) == ExitCode.OK
    out = capsys.readouterr().out
    assert "nothing to follow up on" in out
    assert "followed up)" not in out, "no empty summary under a header"
