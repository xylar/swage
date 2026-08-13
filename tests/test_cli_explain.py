"""Tests for `swage explain` (DESIGN.md 9.2).

The claim worth testing is not the layout -- `render_explain` has its own
tests -- but the input. `explain` renders a *stored* record and never
recomputes one, because the question is "why did it do that at 03:00" rather
than "what would it do now". So these pin that it reads the artifact, that it
reads the right one, and that it says something useful when there is none.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swage.cli import ExitCode, main
from swage.report import FeedstockRecord, GateRecord, RunRecord, write_run

RECORD = FeedstockRecord(
    feedstock="demo",
    outcome="needs-review",
    detail="G6: trust is 'manual', not 'auto'",
    recipe="v1, 1 output, 2 requirements blocks",
    pull_request=81,
    pull_requests=4,
    head="f7d7401",
    python_min="3.10",
    python_min_source="linux_64_.yaml",
    gates=(GateRecord(name="G6", passed=False, detail="trust is 'manual'"),),
    decision="needs-review",
)

CLEAN = FeedstockRecord(feedstock="quiet", outcome="unchanged")


def _run(root: Path, stamp: str, *records: FeedstockRecord) -> Path:
    directory = root / "swage" / "runs" / stamp
    write_run(
        RunRecord(command="swage scan --all", started=stamp, feedstocks=records),
        directory,
    )
    return directory


def test_it_renders_the_record_of_the_most_recent_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    _run(
        tmp_path,
        "2026-08-01T00-00-00",
        FeedstockRecord(feedstock="demo", outcome="unchanged"),
    )
    _run(tmp_path, "2026-08-12T19-51-57", RECORD)

    code = main(["explain", "demo"])

    out = capsys.readouterr().out
    # The newer run's record, not the older one's.
    assert "run 2026-08-12T19-51-57" in out
    assert "G6 FAIL" in out
    assert "VERDICT  needs-review" in out
    # The exit code the sweep gave this feedstock, asked one at a time.
    assert code == ExitCode.NEEDS_REVIEW


def test_an_older_run_can_be_named(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ "Why did it do that" is often asked about a run that is not the last one."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    older = _run(tmp_path, "2026-08-01T00-00-00", RECORD)
    _run(tmp_path, "2026-08-12T19-51-57", CLEAN)

    code = main(["explain", "demo", "--from-run", str(older)])

    assert "run 2026-08-01T00-00-00" in capsys.readouterr().out
    assert code == ExitCode.NEEDS_REVIEW


def test_a_feedstock_that_needs_nothing_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    _run(tmp_path, "2026-08-12T19-51-57", CLEAN)

    assert main(["explain", "quiet"]) == ExitCode.OK
    assert "swage explain quiet" in capsys.readouterr().out


def test_json_prints_the_stored_record_verbatim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The human and machine views are two renderings of one object.

    Not two implementations of it -- anything reading this is reading the same
    thing `run.json` holds, so it cannot drift from what the run recorded.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    directory = _run(tmp_path, "2026-08-12T19-51-57", RECORD)

    main(["explain", "demo", "--json"])

    printed = json.loads(capsys.readouterr().out)
    stored = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    assert printed == stored["feedstocks"][0]


def test_no_run_at_all_says_to_scan_rather_than_recomputing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """There is no answer to "why did it do that" if it never did anything."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    assert main(["explain", "demo"]) == ExitCode.FAILED
    assert "run `swage scan` first" in capsys.readouterr().err


def test_a_half_written_run_directory_is_not_the_latest_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A run that died leaves the directory behind with no artifact in it."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    _run(tmp_path, "2026-08-01T00-00-00", RECORD)
    (tmp_path / "swage" / "runs" / "2026-08-12T19-51-57").mkdir(parents=True)

    assert main(["explain", "demo"]) == ExitCode.NEEDS_REVIEW
    assert "run 2026-08-01T00-00-00" in capsys.readouterr().out


def test_a_feedstock_the_run_never_saw_names_the_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    _run(tmp_path, "2026-08-12T19-51-57", RECORD)

    assert main(["explain", "absent"]) == ExitCode.FAILED
    err = capsys.readouterr().err
    assert "no record of 'absent'" in err
    assert "swage scan --all" in err


def test_explaining_needs_no_config_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A typo in the quirks database must not become the answer.

    `explain` reads a run directory and nothing else -- no config, no network,
    no recipe -- so an unrelated config error cannot stand between someone and
    the record of what swage already did.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("SWAGE_CONFIG_ROOT", str(tmp_path / "not-a-config-tree"))
    _run(tmp_path, "2026-08-12T19-51-57", RECORD)

    assert main(["explain", "demo"]) == ExitCode.NEEDS_REVIEW
    assert "G6 FAIL" in capsys.readouterr().out
