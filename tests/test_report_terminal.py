"""Tests for the grouped terminal summary (DESIGN.md 9).

The example in DESIGN.md 9 is the specification, so the first test builds the
run it depicts and checks the rendering against it. That is a claim about the
spec rather than about my expectations, which is the same reason the planner
is tested against the corpus.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from swage.report import FeedstockRecord, RunRecord, render_summary, supports_color


def _run(*records: FeedstockRecord, command: str = "", started: str = "") -> RunRecord:
    return RunRecord(command=command, started=started, feedstocks=records)


def _many(outcome: str, count: int, prefix: str = "f") -> list[FeedstockRecord]:
    return [
        FeedstockRecord(feedstock=f"{prefix}{i}", outcome=outcome)  # type: ignore[arg-type]
        for i in range(count)
    ]


def test_the_summary_matches_the_example_in_the_design() -> None:
    run = _run(
        *_many("merged", 28, "m"),
        *_many("merge-ready", 41, "r"),
        *_many("awaiting-ci", 13, "a"),
        *_many("proposed", 12, "p"),
        FeedstockRecord(
            feedstock="google-cloud-aiplatform",
            outcome="needs-review",
            detail="G3: undeclared upstream extra 'evaluation'",
        ),
        FeedstockRecord(
            feedstock="google-cloud-bigquery",
            outcome="needs-review",
            detail="G2: unresolved name 'db-dtypes'",
        ),
        FeedstockRecord(
            feedstock="google-cloud-spanner",
            outcome="degraded",
            detail="label API call failed after 3 attempts",
        ),
        *_many("migrated", 3, "g"),
        *_many("needs-migration", 18, "n"),
        *_many("unchanged", 206, "u"),
        FeedstockRecord(
            feedstock="markupsafe",
            outcome="failed",
            detail="unsupported build-variant switch 'use_noarch'",
        ),
        command="swage update --family google-cloud",
        started="2026-08-11T14:02:00Z",
    )
    lines = render_summary(run, width=88, color=False).splitlines()

    header = lines[0]
    assert header.startswith("swage update --family google-cloud    2026-08-11 14:02")
    assert header.endswith("(325 scanned)")
    assert len(header) == 88
    assert lines[1] == ""

    assert lines[2:] == [
        "  MERGED (28)          path B: no changes needed, CI already green, merged",
        "  MERGE-READY (41)     path A: pushed + labeled automerge, awaiting CI",
        "  AWAITING CI (13)     path B candidates, CI still running -- `swage status` later",  # noqa: E501
        "  PROPOSED (12)        pushed, needs your review before labeling",
        "  NEEDS REVIEW (2)",
        "    google-cloud-aiplatform  G3: undeclared upstream extra 'evaluation'",
        "    google-cloud-bigquery    G2: unresolved name 'db-dtypes'",
        "  DEGRADED (1)         pushed but NOT labeled -- rerun `swage status`",
        "    google-cloud-spanner     label API call failed after 3 attempts",
        "  MIGRATED (3)         v0 -> v1 converted and updated -- review both commits",
        "  NEEDS MIGRATION (18) v0 meta.yaml -- rerun with `--migrate` to convert in place",  # noqa: E501
        "  UNCHANGED (206)      no open bot PR",
        "  FAILED (1)",
        "    markupsafe               unsupported build-variant switch 'use_noarch'",
    ]


def test_an_empty_bucket_is_not_printed() -> None:
    """A run over one family should not list eight outcomes it cannot produce."""
    rendered = render_summary(
        _run(FeedstockRecord(feedstock="x", outcome="unchanged")), color=False
    )
    assert "UNCHANGED (1)" in rendered
    assert "MERGED" not in rendered
    assert "FAILED" not in rendered


def test_only_feedstocks_with_something_to_say_are_listed() -> None:
    """206 lines of "no open bot PR" would bury the nine that need reading."""
    rendered = render_summary(
        _run(*_many("unchanged", 206, "u")), width=88, color=False
    )
    assert "UNCHANGED (206)" in rendered
    assert "u0" not in rendered


def test_a_long_detail_wraps_under_itself_rather_than_beside() -> None:
    run = _run(
        FeedstockRecord(
            feedstock="google-cloud-kms",
            outcome="needs-review",
            detail=(
                "G1: 'grpcio-gcp' in recipe, in no upstream version -- declare "
                "in add_requirements or drop"
            ),
        )
    )
    body = render_summary(run, width=76, color=False).splitlines()[2:]
    detail = [line for line in body if line.startswith("    ")]
    assert len(detail) == 2
    assert detail[0].startswith("    google-cloud-kms  G1:")
    # The continuation lines up under the detail, not under the name.
    assert detail[1].startswith(" " * detail[0].index("G1:"))
    assert all(len(line) <= 76 for line in detail)


def test_the_counts_are_of_feedstocks_scanned() -> None:
    rendered = render_summary(
        _run(*_many("unchanged", 3), started="2026-08-11T14:02:00Z"), color=False
    )
    assert "(3 scanned)" in rendered.splitlines()[0]


def test_the_run_directory_is_printed_when_there_is_one(tmp_path: Path) -> None:
    """Outside the home directory it prints in full, separators and all.

    Anchored at the drive root rather than under `tmp_path`, because on
    GitHub's Windows runners the temp directory *is* inside the home
    directory and would be abbreviated.
    """
    directory = Path(tmp_path.anchor) / "swage-runs" / "2026-08-11T14-02"
    rendered = render_summary(
        _run(FeedstockRecord(feedstock="x", outcome="unchanged")),
        run_directory=directory,
        color=False,
    )
    assert rendered.splitlines()[-1] == f"  run: {directory}{os.sep}"


def test_a_directory_under_home_is_abbreviated_in_native_separators(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    r"""`~/AppData\Local\Temp` is a path in two conventions and pastes nowhere."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    rendered = render_summary(
        _run(FeedstockRecord(feedstock="x", outcome="unchanged")),
        run_directory=tmp_path / "cache" / "swage" / "runs" / "2026-08-11T14-02",
        color=False,
    )
    expected = Path("~") / "cache" / "swage" / "runs" / "2026-08-11T14-02"
    assert rendered.splitlines()[-1] == f"  run: {expected}{os.sep}"


def test_colour_wraps_only_the_heading() -> None:
    run = _run(FeedstockRecord(feedstock="markupsafe", outcome="failed", detail="boom"))
    rendered = render_summary(run, width=88, color=True)
    assert "\033[1;31mFAILED (1)\033[0m" in rendered
    # The feedstock line stays plain, so piping to grep keeps working.
    assert "    markupsafe  boom" in rendered


def test_colour_is_off_when_asked_not_to() -> None:
    run = _run(FeedstockRecord(feedstock="x", outcome="failed", detail="boom"))
    assert "\033[" not in render_summary(run, color=False)


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"NO_COLOR": "1", "CLICOLOR_FORCE": "1"}, False),
        ({"CLICOLOR_FORCE": "1"}, True),
        ({"CLICOLOR_FORCE": "0"}, False),
        ({}, False),
    ],
)
def test_colour_detection_follows_the_usual_environment_rules(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: bool
) -> None:
    """NO_COLOR wins over everything, which is what the standard says."""
    for name in ("NO_COLOR", "CLICOLOR_FORCE", "TERM"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert supports_color(stream=None) is expected
