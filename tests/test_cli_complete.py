"""Tests for `swage completion` (DESIGN.md 12.3).

The completer is driven the way the shell drives it: argcomplete's finder is
given the line and the cursor through the environment variables the hook
sets, and what it writes back is what TAB would offer. The names it offers
come from a cache written by `remember` under the `XDG_CACHE_HOME` the suite
already points elsewhere.

One test runs the whole thing as a subprocess through `python -m swage`,
because the callback's cost is the import of the CLI and only a fresh
process pays it; what that import may consist of is `test_cli.py`'s.
"""

from __future__ import annotations

import importlib
import io
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from argcomplete.finders import CompletionFinder

from swage.cli import ExitCode, main
from swage.cli.complete import (
    FAMILIES,
    FEEDSTOCKS,
    SHELLS,
    hook,
    install,
    names_directory,
    recall,
    remember,
)
from swage.cli.main import build_parser
from swage.cli.pipeline import select_feedstocks
from swage.config import load_config
from swage.forge import GitHub

from .conftest import CONFIG_ROOT, REPO_ROOT

#: `swage.cli` re-exports a function called `main`, which shadows the module of
#: that name, so the module has to be imported rather than reached through it.
CLI = importlib.import_module("swage.cli.main")

FEEDSTOCK_NAMES = (
    "globus-cli",
    "google-ads",
    "google-cloud-bigquery",
    "weaviate-client",
)
FAMILY_NAMES = ("airflow-providers", "google-cloud", "microsoft-kiota")


@pytest.fixture
def cached() -> None:
    """A name cache where the completer will look for it."""
    remember(FEEDSTOCKS, FEEDSTOCK_NAMES)
    remember(FAMILIES, FAMILY_NAMES)


def _asking(monkeypatch: pytest.MonkeyPatch, line: str) -> None:
    """The environment the hook sets: the line so far, and the cursor at its end."""
    monkeypatch.setenv("_ARGCOMPLETE", "1")
    monkeypatch.setenv("COMP_LINE", line)
    monkeypatch.setenv("COMP_POINT", str(len(line)))


class _Finder(CompletionFinder):
    def _init_debug_stream(self) -> None:
        """argcomplete's default wraps descriptor 9, which under pytest is pytest's.

        Wrapping it in a file object closes it when that object is collected,
        and the suite then fails on teardown with a bad file descriptor.
        """


def _complete(monkeypatch: pytest.MonkeyPatch, line: str) -> list[str]:
    """What TAB offers at the end of ``line``.

    A unique match comes back with the space the shell would type after it,
    and is compared without it.
    """
    _asking(monkeypatch, line)
    parser = build_parser()
    install(parser)
    offered = io.StringIO()
    _Finder()(parser, output_stream=offered, exit_method=lambda code: None)
    return offered.getvalue().split()


def test_completes_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    offered = _complete(monkeypatch, "swage ")

    assert {"scan", "audit", "update", "explain", "completion"} <= set(offered)
    assert _complete(monkeypatch, "swage sc") == ["scan"]


def test_completes_the_options_of_the_command_it_is_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert set(_complete(monkeypatch, "swage scan --f")) == {
        "--feedstock",
        "--family",
    }
    offered = _complete(monkeypatch, "swage update --")
    assert "--all" in offered
    assert "--dry-run" in offered


def test_completes_the_names(monkeypatch: pytest.MonkeyPatch, cached: None) -> None:
    assert _complete(monkeypatch, "swage scan --family ") == list(FAMILY_NAMES)
    assert _complete(monkeypatch, "swage audit --feedstock goo") == [
        "google-ads",
        "google-cloud-bigquery",
    ]
    # The positional, which is the whole of what `explain` and `draft` take.
    assert _complete(monkeypatch, "swage explain glo") == ["globus-cli"]
    assert _complete(monkeypatch, "swage draft glo") == ["globus-cli"]


def test_completes_a_second_feedstock(
    monkeypatch: pytest.MonkeyPatch, cached: None
) -> None:
    """`--feedstock a b c` takes names until the next option (design-v1.md 8).

    The flag that most wants several names must not complete exactly one.
    """
    line = "swage update --feedstock google-ads glo"

    assert _complete(monkeypatch, line) == ["globus-cli"]


def test_reads_past_a_directory_that_shares_a_command_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--config-root config` names a directory, not the `config` command.

    And `config` is what the maintainer's tree is called, so this is the
    ordinary spelling rather than a contrived one.
    """
    line = "swage --config-root config s"

    assert set(_complete(monkeypatch, line)) == {"scan", "status"}


def test_completes_a_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config.yaml").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert _complete(monkeypatch, "swage --config-root con") == ["config/"]


def test_offers_nothing_it_cannot_enumerate(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--since 7d` has no candidates, and filenames would be worse than none."""
    assert _complete(monkeypatch, "swage status --since ") == []


def test_offers_the_shells_completion_itself_takes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert set(SHELLS) <= set(_complete(monkeypatch, "swage completion "))


def test_the_hook_answers_a_tab_through_the_entry_point(tmp_path: Path) -> None:
    """The whole callback, in the process the shell would start.

    `_ARGCOMPLETE=3` is the hook's spelling for `python -m swage`, and the
    file is where argcomplete writes when told to instead of to descriptor 8,
    which keeps this off the shell and on every platform CI runs.
    """
    remember(FEEDSTOCKS, FEEDSTOCK_NAMES)
    answer = tmp_path / "answer"
    line = f"{sys.executable} -m swage explain wea"
    environment = {
        **os.environ,
        "_ARGCOMPLETE": "3",
        "_ARGCOMPLETE_STDOUT_FILENAME": str(answer),
        "COMP_LINE": line,
        "COMP_POINT": str(len(line)),
    }

    finished = subprocess.run(
        [sys.executable, "-m", "swage"],
        env=environment,
        cwd=REPO_ROOT / "src",
        capture_output=True,
        text=True,
        check=False,
    )

    assert finished.returncode == 0, finished.stderr
    assert answer.read_text(encoding="utf-8").split() == ["weaviate-client"]


@pytest.mark.parametrize("shell", SHELLS)
def test_the_hook_registers_swage(shell: str) -> None:
    printed = hook(shell)

    assert "swage" in printed.splitlines()[0]
    assert "_python_argcomplete" in printed


@pytest.mark.parametrize("shell", SHELLS)
def test_the_hook_parses(shell: str, tmp_path: Path) -> None:
    """`-n` reads the whole script, where the shell is there to read it."""
    path = tmp_path / f"swage.{shell}"
    path.write_text(hook(shell), encoding="utf-8")

    subprocess.run([_shell(shell), "-n", str(path)], check=True)


def _shell(name: str) -> str:
    """The shell, or a skip: CI runs on Windows too, and has no zsh anywhere.

    Windows is recognized by `os.name` rather than by `sys.platform`, which
    mypy resolves statically -- so on the one platform this skip exists for,
    every line below it is unreachable and `warn_unreachable` fails the build.
    """
    if os.name != "posix":
        pytest.skip("the hook is for POSIX shells")
    found = shutil.which(name)
    if found is None:
        pytest.skip(f"{name} is not installed")
    return found


def test_names_survive_a_round_trip(tmp_path: Path) -> None:
    remember(FEEDSTOCKS, ["b", "a", "a"], root=tmp_path)

    assert recall(FEEDSTOCKS, root=tmp_path) == ("a", "b")
    assert recall(FAMILIES, root=tmp_path) == ()


def test_a_cache_that_cannot_be_written_is_not_an_error(tmp_path: Path) -> None:
    """The run this was a side effect of did its job; completion offers less."""
    blocked = tmp_path / "file"
    blocked.write_text("", encoding="utf-8")

    remember(FEEDSTOCKS, ["a"], root=blocked)

    assert recall(FEEDSTOCKS, root=blocked) == ()


def test_printing_the_hook_reads_no_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A maintainer installing completion is standing wherever they were.

    Every other command loads the quirks database first, and a `completion`
    that failed outside a config tree would read as a broken swage.
    """
    monkeypatch.setenv("SWAGE_CONFIG_ROOT", str(tmp_path / "not-a-config-tree"))
    monkeypatch.chdir(tmp_path)

    assert main(["completion", "bash"]) == ExitCode.OK
    assert "_python_argcomplete" in capsys.readouterr().out


def test_completion_wants_a_shell_or_a_refresh(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["completion"])

    assert excinfo.value.code == 2
    assert "one of the arguments" in capsys.readouterr().err


def test_a_run_that_discovers_remembers_the_whole_fleet() -> None:
    """A `--family` run has the whole answer in hand while acting on part of it.

    Remembering only what the run covered would make completion narrower every
    time swage was used on a family, which is most of how it is used.
    """
    tree = load_config(CONFIG_ROOT)

    covered = select_feedstocks(GitHub(run=_teams), tree, family="google-cloud")

    assert covered == ("google-cloud-bigquery",)
    assert recall(FEEDSTOCKS) == ("globus-cli", "google-cloud-bigquery")


def test_refresh_records_what_it_discovered(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one command whose whole purpose is that cache."""
    monkeypatch.setenv("SWAGE_CONFIG_ROOT", str(CONFIG_ROOT))
    monkeypatch.setattr("swage.forge.GitHub", lambda: GitHub(run=_teams))

    assert main(["completion", "--refresh"]) == ExitCode.OK

    assert recall(FEEDSTOCKS) == ("globus-cli", "google-cloud-bigquery")
    # Families come from the tree every command loads, so what the refresh adds
    # is the one GitHub call.
    assert "google-cloud" in recall(FAMILIES)
    out = capsys.readouterr().out
    assert "2 feedstocks" in out
    assert str(names_directory()) in out


def _teams(argv: Sequence[str]) -> str:
    assert argv[-1] == "user/teams"
    return json.dumps(
        [
            [
                {"name": name, "organization": {"login": "conda-forge"}}
                for name in ("google-cloud-bigquery", "globus-cli")
            ]
        ]
    )
