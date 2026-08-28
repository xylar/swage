"""Tests for `swage completion` (DESIGN.md 8.3).

The generated scripts are run rather than only read. A completion script is
shell that nothing type checks, and its failure mode is silence -- a script
with a syntax error in one branch completes every other branch perfectly, so
"the commands still complete" says nothing about whether feedstock names do.
So bash drives the real function through `COMP_WORDS`, and the names it offers
come from a cache written by `remember` under a real `XDG_CACHE_HOME`, which is
what holds the shell's copy of the cache path to `cache.py`'s.

zsh is syntax-checked where it exists and skipped where it does not, which is
everywhere CI runs. That leaves `_arguments` semantics untested rather than the
generation, and the generation is what changes.
"""

from __future__ import annotations

import importlib
import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from swage.cli import ExitCode, main
from swage.cli.complete import (
    FAMILIES,
    FEEDSTOCKS,
    SHELLS,
    Values,
    completion_script,
    describe,
    names_directory,
    recall,
    remember,
)
from swage.cli.consider import select_feedstocks
from swage.cli.main import build_parser
from swage.config import load_config
from swage.forge import GitHub

from .conftest import CONFIG_ROOT

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
def cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A name cache where the generated script will look for it."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    remember(FEEDSTOCKS, FEEDSTOCK_NAMES)
    remember(FAMILIES, FAMILY_NAMES)
    return tmp_path


def _shell(name: str) -> str:
    """The shell, or a skip: CI runs on Windows too, and has no zsh anywhere.

    Windows is recognized by `os.name` rather than by `sys.platform`, which
    mypy resolves statically -- so on the one platform this skip exists for,
    every line below it is unreachable and `warn_unreachable` fails the build.
    """
    if os.name != "posix":
        pytest.skip("the generated scripts are for POSIX shells")
    found = shutil.which(name)
    if found is None:
        pytest.skip(f"{name} is not installed")
    return found


def _script(shell: str, directory: Path) -> Path:
    path = directory / f"swage.{shell}"
    path.write_text(completion_script(shell, build_parser()), encoding="utf-8")
    return path


def _complete(script: Path, words: Sequence[str]) -> list[str]:
    """What bash offers for ``words``, whose last entry is the word being typed.

    The environment is inherited, `XDG_CACHE_HOME` included, which is the point
    -- the script has to find the cache from the same variable `cache_root`
    reads, and a test that passed the path in would not check that.
    """
    listed = " ".join(shlex.quote(word) for word in words)
    driver = (
        f"source {shlex.quote(str(script))}\n"
        f"COMP_WORDS=({listed})\n"
        f"COMP_CWORD={len(words) - 1}\n"
        "_swage\n"
        'printf "%s\\n" "${COMPREPLY[@]}"\n'
    )
    finished = subprocess.run(
        [_shell("bash"), "-c", driver], capture_output=True, text=True, check=True
    )
    return [line for line in finished.stdout.splitlines() if line]


def test_every_command_can_be_completed() -> None:
    """A command the scripts do not know is one TAB will never offer.

    Checked against the parser rather than against a list written here, so that
    adding a command to swage cannot leave completion behind.
    """
    line = describe(build_parser())
    commands = set(line.names)
    assert {"scan", "audit", "update", "draft", "explain", "status"} <= commands

    for shell in SHELLS:
        script = completion_script(shell, build_parser())
        for command in commands:
            assert command in script, f"{shell} completion does not offer {command}"


def test_values_are_classified_by_what_they_name() -> None:
    """What completes in a place is a property of the value, not of the flag."""
    commands = {command.name: command for command in describe(build_parser()).commands}
    scan = {
        flag: option.argument
        for option in commands["scan"].options
        for flag in option.flags
    }
    status = {
        flag: option.argument
        for option in commands["status"].options
        for flag in option.flags
    }

    assert scan["--feedstock"].values is Values.FEEDSTOCK
    assert scan["--feedstock"].repeated
    assert scan["--family"].values is Values.FAMILY
    assert scan["--all"].values is Values.NONE
    # A window is a value swage cannot enumerate, and says so rather than
    # falling through to whatever the default completion would offer.
    assert status["--since"].values is Values.OPAQUE

    explain = commands["explain"].positional
    assert explain is not None and explain.values is Values.FEEDSTOCK
    shells = commands["completion"].positional
    assert shells is not None and shells.choices == SHELLS


@pytest.mark.parametrize("shell", SHELLS)
def test_the_generated_script_parses(shell: str, tmp_path: Path) -> None:
    """`-n` reads the whole script, so a branch nothing exercises fails here.

    Worth having for both shells: a generator emitting `case $prev in )` for a
    kind of value swage happens to have no option for would still pass every
    test that only drove the branches it does emit.
    """
    subprocess.run([_shell(shell), "-n", str(_script(shell, tmp_path))], check=True)


def test_bash_completes_commands(cached: Path) -> None:
    script = _script("bash", cached)

    offered = _complete(script, ["swage", ""])

    assert {"scan", "audit", "update", "explain"} <= set(offered)
    assert _complete(script, ["swage", "sc"]) == ["scan"]


def test_bash_completes_the_options_of_the_command_it_is_in(cached: Path) -> None:
    script = _script("bash", cached)

    assert set(_complete(script, ["swage", "scan", "--f"])) == {
        "--feedstock",
        "--family",
    }
    # `update` has no `--all`, deliberately (DESIGN.md 8), and a flag TAB
    # offers reads as a flag that exists.
    assert "--all" not in _complete(script, ["swage", "update", "--"])
    # `--execute` does exist and still works, but it is hidden from `--help`
    # because it is retired, and completing it would teach the spelling that
    # stopped being the one to type.
    assert "--execute" not in _complete(script, ["swage", "update", "--"])
    assert "--dry-run" in _complete(script, ["swage", "update", "--"])


def test_bash_completes_the_names(cached: Path) -> None:
    script = _script("bash", cached)

    assert _complete(script, ["swage", "scan", "--family", ""]) == list(FAMILY_NAMES)
    assert _complete(script, ["swage", "audit", "--feedstock", "goo"]) == [
        "google-ads",
        "google-cloud-bigquery",
    ]
    # The positional, which is the whole of what `explain` and `draft` take.
    assert _complete(script, ["swage", "explain", "glo"]) == ["globus-cli"]
    assert _complete(script, ["swage", "draft", "glo"]) == ["globus-cli"]


def test_bash_completes_a_second_feedstock(cached: Path) -> None:
    """`--feedstock a b c` takes names until the next option (DESIGN.md 8).

    Without this the second name would be completed as the command's
    positional, which for `update` is nothing at all -- so the flag that most
    wants several names would complete exactly one.
    """
    script = _script("bash", cached)

    words = ["swage", "update", "--feedstock", "google-ads", "glo"]

    assert _complete(script, words) == ["globus-cli"]


def test_bash_reads_past_a_directory_that_shares_a_command_name(cached: Path) -> None:
    """`--config-root config` names a directory, not the `config` command.

    And `config` is what the maintainer's tree is called, so this is the
    ordinary spelling rather than a contrived one.
    """
    script = _script("bash", cached)

    words = ["swage", "--config-root", "config", "s"]

    assert set(_complete(script, words)) == {"scan", "status"}


def test_bash_offers_nothing_it_cannot_enumerate(cached: Path) -> None:
    """`--since 7d` has no candidates, and filenames would be worse than none."""
    script = _script("bash", cached)

    assert _complete(script, ["swage", "status", "--since", ""]) == []


def test_bash_offers_the_shells_completion_itself_takes(cached: Path) -> None:
    script = _script("bash", cached)

    assert set(SHELLS) <= set(_complete(script, ["swage", "completion", ""]))


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


def test_printing_a_script_reads_no_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A maintainer installing completion is standing wherever they were.

    Every other command loads the quirks database first, and a `completion`
    that failed outside a config tree would read as a broken swage.
    """
    monkeypatch.setenv("SWAGE_CONFIG_ROOT", str(tmp_path / "not-a-config-tree"))
    monkeypatch.chdir(tmp_path)

    assert main(["completion", "bash"]) == ExitCode.OK
    assert "complete -F _swage swage" in capsys.readouterr().out


def test_completion_wants_a_shell_or_a_refresh(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["completion"])

    assert excinfo.value.code == 2
    assert "one of the arguments" in capsys.readouterr().err


def test_a_run_that_discovers_remembers_the_whole_fleet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `--family` run has the whole answer in hand while acting on part of it.

    Remembering only what the run covered would make completion narrower every
    time swage was used on a family, which is most of how it is used.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    tree = load_config(CONFIG_ROOT)

    covered = select_feedstocks(GitHub(run=_teams), tree, family="google-cloud")

    assert covered == ("google-cloud-bigquery",)
    assert recall(FEEDSTOCKS) == ("globus-cli", "google-cloud-bigquery")


def test_refresh_records_what_it_discovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one command whose whole purpose is that cache."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("SWAGE_CONFIG_ROOT", str(CONFIG_ROOT))
    monkeypatch.setattr(CLI, "GitHub", lambda: GitHub(run=_teams))

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
