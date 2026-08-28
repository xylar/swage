"""Tests for the command line (DESIGN.md 8, 9.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from swage.cli import ExitCode, main
from swage.cli.complete import describe
from swage.cli.main import _PLANNED, _command_line
from swage.cli.main import build_parser as _parser

from .conftest import CONFIG_ROOT


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    assert "swage" in capsys.readouterr().out


def test_no_command_is_listed_that_does_not_work() -> None:
    """`--help` should describe the whole tool without pretending it works.

    `migrate` was the last of these and works now, so the list is empty --
    which is the assertion rather than a reason to delete the test. The
    mechanism is how the next unbuilt command reaches `--help` honestly, and
    what this guards is a list quietly regrowing entries nobody notices.
    """
    assert _PLANNED == {}


@pytest.mark.parametrize("command", ["scan", "update"])
def test_a_command_that_writes_or_sweeps_requires_a_selector(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Neither is something to trip into by typing the command alone."""
    with pytest.raises(SystemExit) as excinfo:
        main([command])
    assert excinfo.value.code == 2
    assert "one of the arguments" in capsys.readouterr().err


def test_migrate_requires_a_feedstock(capsys: pytest.CaptureFixture[str]) -> None:
    """It takes names positionally, so argparse asks rather than sweeping.

    148 feedstocks are still on the old format and a bare `swage migrate`
    reading all of them would be the same unintended sweep the selector rules
    exist to prevent.
    """
    with pytest.raises(SystemExit) as excinfo:
        main(["migrate"])
    assert excinfo.value.code == 2
    assert "required: FEEDSTOCK" in capsys.readouterr().err


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
    assert (
        "one of the arguments -f/--feedstock -m/--family --all is required"
        in capsys.readouterr().err
    )


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
        "    - line: freetds\n"
        "      reason: pymssql links against it\n"
        "constraints:\n"
        '  numpy:\n    bound: "<3"\n    reason: numpy 3 has not been tested here\n'
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
    # The reason, because an added requirement is exactly as good as it.
    assert "pymssql links against it" in out
    assert "constraint:        numpy <3" in out
    assert "numpy 3 has not been tested here" in out
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
    "command",
    ["config", "scan", "audit", "update", "explain", "status", "draft", "completion"],
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


@pytest.mark.parametrize("command", ["config", "scan", "audit", "update"])
def test_feedstock_can_be_given_more_than_once(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both spellings, because both are things a person will type.

    argparse kept only the last `--feedstock`, so naming two feedstocks acted
    on one and reported `(1 scanned)` without mentioning the other. This checks
    the parser accepts them; `select_feedstocks` is where the coverage is.
    """
    parser = _parser()

    repeated = parser.parse_args([command, "--feedstock", "a", "--feedstock", "b"])
    together = parser.parse_args([command, "--feedstock", "a", "b"])

    assert repeated.feedstock == ["a", "b"]
    assert together.feedstock == ["a", "b"]


def test_the_selectors_carry_the_same_letter_under_every_command() -> None:
    """`-f` and `-m` mean the same thing wherever they appear, or not at all.

    A short option that four commands out of five have is worse than none:
    the one that does not have it fails on a word the other four accept, and
    the failure looks like the name being wrong rather than the flag.

    `-m` rather than the obvious `-F` for the family. The two select different
    things -- one names feedstocks, the other matches a glob that can be fifty
    -- and a pair differing by the shift key alone is a typo away from each
    other on the command that writes. `-a` stays free for `--all`.
    """
    commands = describe(_parser()).commands
    by_flag = {
        long: [
            command.name
            for command in commands
            for option in command.options
            if long in option.flags
        ]
        for long in ("--feedstock", "--family")
    }
    assert by_flag["--feedstock"] == ["config", "scan", "audit", "update"]
    assert by_flag["--family"] == ["scan", "audit", "update", "draft"]
    for command in commands:
        for option in command.options:
            if "--feedstock" in option.flags:
                assert option.flags == ("-f", "--feedstock"), command.name
            if "--family" in option.flags:
                assert option.flags == ("-m", "--family"), command.name

    parser = _parser()
    assert parser.parse_args(["audit", "-f", "a", "b"]).feedstock == ["a", "b"]
    assert parser.parse_args(["audit", "-m", "google-cloud"]).family == "google-cloud"


def test_the_header_names_every_feedstock_given() -> None:
    """The header is how a reader checks swage understood the command.

    One naming a single feedstock above a run that covered two is worse than
    no header at all, and is how the dropped-argument bug stayed invisible.
    """
    args = _parser().parse_args(["audit", "--feedstock", "a", "b"])

    assert _command_line(args) == "swage audit --feedstock a b"


def test_the_header_says_when_a_conversion_was_in_scope() -> None:
    """`run.json` has to be able to tell the two runs apart.

    A run where every v0 feedstock was reported and skipped and one where each
    was converted differ in what they did to other people's repositories, and
    the recorded command is where that is written down.
    """
    parser = _parser()

    args = parser.parse_args(["update", "--feedstock", "demo", "--migrate"])
    assert _command_line(args) == "swage update --feedstock demo --migrate"

    args = parser.parse_args(["update", "--feedstock", "demo"])
    assert _command_line(args) == "swage update --feedstock demo"


def test_migrate_is_not_the_default_for_update() -> None:
    """Converting several hundred feedstocks is not something to trip into."""
    args = _parser().parse_args(["update", "--family", "google-cloud"])
    assert args.migrate is False


def test_only_the_command_that_writes_nothing_can_read_from_the_cache(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--cached` replays a fleet that is deliberately out of date.

    Harmless to a report and not to a push, so it is offered on `audit`, which
    writes nothing (DESIGN.md 8.2), and on nothing else. A command that pushes
    or labels has no spelling for it at all rather than one that is refused.
    """
    parser = _parser()
    assert parser.parse_args(["audit", "--all", "--cached"]).cached is True
    assert parser.parse_args(["audit", "--all"]).cached is False
    for command in ("scan", "update"):
        with pytest.raises(SystemExit):
            parser.parse_args([command, "--feedstock", "demo", "--cached"])
        assert "unrecognized arguments" in capsys.readouterr().err


def test_a_replayed_audit_says_so_in_the_run_it_writes() -> None:
    """`run.json` outlives the terminal, and `swage explain` reads it back.

    A replayed audit reports the fleet as it was when the cache was recorded,
    so a run record that did not say so could be read months later as a report
    on the fleet as it stood. `--quiet` is deliberately not recorded: this
    header says what changed the run, not what changed the display.
    """
    parser = _parser()
    replayed = _command_line(parser.parse_args(["audit", "--all", "--cached"]))
    assert replayed == "swage audit --all --cached"
    live = _command_line(parser.parse_args(["audit", "--all", "--quiet"]))
    assert live == "swage audit --all"
