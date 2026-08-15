"""Tests for the command line (DESIGN.md 8, 9.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from swage.cli import ExitCode, main

from .conftest import CONFIG_ROOT


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    assert "swage" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["migrate"])
def test_planned_commands_are_listed_but_fail(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--help` should describe the whole tool without pretending it works."""
    assert main([command]) == ExitCode.FAILED
    assert "not implemented yet" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["scan", "update"])
def test_a_command_that_writes_or_sweeps_requires_a_selector(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Neither is something to trip into by typing the command alone."""
    with pytest.raises(SystemExit) as excinfo:
        main([command])
    assert excinfo.value.code == 2
    assert "one of the arguments" in capsys.readouterr().err


def test_update_has_no_all_selector(capsys: pytest.CaptureFixture[str]) -> None:
    """DESIGN.md 8 gives `--all` to the commands that read, and to no other.

    Sweeping every feedstock is what reading is for; a fleet-wide *write* is
    not a gesture that should have a spelling this short.
    """
    with pytest.raises(SystemExit) as excinfo:
        main(["update", "--all"])
    assert excinfo.value.code == 2
    assert "--all" not in capsys.readouterr().err.partition("\n")[0]


def test_scan_requires_a_selector(capsys: pytest.CaptureFixture[str]) -> None:
    """A bare `swage scan` would sweep every feedstock the maintainer has.

    That is a real operation against GitHub, so it takes saying `--all` rather
    than typing the command with no arguments (DESIGN.md 8).
    """
    with pytest.raises(SystemExit) as excinfo:
        main(["scan"])
    assert excinfo.value.code == 2
    assert "--feedstock --family --all is required" in capsys.readouterr().err


def test_config_validates_the_shipped_tree(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config-root", str(CONFIG_ROOT), "config"]) == ExitCode.OK
    out = capsys.readouterr().out
    assert "airflow-providers" in out
    assert "google-cloud-bigquery" in out


def test_config_shows_one_feedstock(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(
        [
            "--config-root",
            str(CONFIG_ROOT),
            "config",
            "--feedstock",
            "apache-airflow-providers-common-sql",
        ]
    )
    assert exit_code == ExitCode.OK
    out = capsys.readouterr().out
    assert "family:            airflow-providers" in out
    assert "config/name-map.yaml" in out


def test_config_shows_every_key_that_applies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command the documentation sends a maintainer to after an edit.

    It printed seven of the fifteen keys, so a freshly written
    `add_requirements` or `constraints` resolved perfectly and appeared
    nowhere -- which reads exactly like an edit that did not land, and sends
    the reader looking for a mistake they did not make.
    """
    root = tmp_path / "config"
    (root / "feedstocks").mkdir(parents=True)
    (root / "defaults.yaml").write_text(
        (CONFIG_ROOT / "defaults.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (root / "feedstocks" / "demo.yaml").write_text(
        "feedstock: demo\n"
        "add_requirements:\n"
        "  run:\n"
        "    - freetds\n"
        "constraints:\n"
        '  numpy: "<3"\n'
        "run_constraints:\n"
        "  cryptography:\n"
        "    extra: crypto\n"
        "retire:\n"
        "  - google-api-core\n",
        encoding="utf-8",
    )

    exit_code = main(["--config-root", str(root), "config", "--feedstock", "demo"])

    assert exit_code == ExitCode.OK
    out = capsys.readouterr().out
    assert "freetds  (config/feedstocks/demo.yaml)" in out
    assert "constraint:        numpy <3" in out
    assert "run constraint:    cryptography tracks extra crypto" in out
    assert "retire:            google-api-core" in out
    assert "recipe owned:      python, pip" in out


def test_config_reports_a_bad_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--config-root", str(tmp_path / "nope"), "config"])
    assert exit_code == ExitCode.FAILED
    assert "config root does not exist" in capsys.readouterr().err


def test_config_root_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SWAGE_CONFIG_ROOT", str(CONFIG_ROOT))
    assert main(["config"]) == ExitCode.OK
    assert "config root:" in capsys.readouterr().out


@pytest.mark.parametrize(
    "command", ["config", "scan", "audit", "update", "explain", "status", "draft"]
)
def test_every_command_says_what_it_does_and_shows_an_example(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--help` is the only documentation a maintainer is guaranteed to find.

    A one-line `help=` in the parent listing says which command to reach for;
    it does not say what the command will do to a feedstock, or what to type.
    """
    with pytest.raises(SystemExit):
        main([command, "--help"])
    out = capsys.readouterr().out
    assert "example" in out, f"{command} shows no example"
    assert len(out.splitlines()) > 8, f"{command} has no description"
