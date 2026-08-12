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


@pytest.mark.parametrize("command", ["update", "status", "audit", "migrate"])
def test_planned_commands_are_listed_but_fail(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--help` should describe the whole tool without pretending it works."""
    assert main([command]) == ExitCode.FAILED
    assert "not implemented yet" in capsys.readouterr().err


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
